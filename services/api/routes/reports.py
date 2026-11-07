"""Handler for report listing, retrieval, and trigger endpoints."""

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Key

from marketdata.contracts import AccessDenied, QuotaExceeded, identifier, iso, stable_id
from marketdata.repository import DynamoStore, pk
from services.api.auth import AuthContext
from services.api.response import error, ok, paginated
from services.api.s3 import presign_client


def handle_get_reports(event: dict, store: DynamoStore, auth: AuthContext) -> dict:
    """List generated summaries and AI reports for the tenant."""
    store.tenant(auth.tenant_id)
    items = list(store.pages(
        store.control,
        KeyConditionExpression=Key("pk").eq(pk(auth.tenant_id)) & Key("sk").begins_with("REPORT#"),
        ConsistentRead=True,
    ))

    reports = [
        {
            "report_id": r.get("report_id") or r["sk"].removeprefix("REPORT#"),
            "period": r.get("period") or r.get("date"),
            "date": r.get("period") or r.get("date"),
            "status": r.get("status"),
            "ai_status": r.get("ai_status"),
            "summary": r.get("summary"),
            "created_at": r.get("created_at"),
            "s3_key": r.get("s3_key"),
        }
        for r in items
    ]

    return paginated(
        data=reports,
        total=len(reports),
        page=1,
        limit=20,
        extra={"tenant_id": auth.tenant_id, "count": len(reports), "reports": reports},
    )


MAX_REPORT_BYTES = 1024 * 1024


def stored_report(s3, bucket: str, key: str, report_id: str) -> dict:
    """Read the full report from S3, or {} so the page still shows the stored preview."""
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        if obj.get("ContentLength", 0) > MAX_REPORT_BYTES:
            return {}
        report = json.loads(obj["Body"].read(MAX_REPORT_BYTES + 1))
    except Exception:
        return {}
    return report if isinstance(report, dict) and report.get("report_id") == report_id else {}


def handle_get_report_detail(
    event: dict,
    store: DynamoStore,
    auth: AuthContext,
    s3_client=None,
    reports_bucket_name: str | None = None,
) -> dict:
    """Retrieve report detail or generate a presigned download URL from ReportsBucket."""
    path_params = event.get("pathParameters") or {}
    report_id = path_params.get("report_id") or path_params.get("id")
    if not report_id:
        return error("missing_report_id", "report_id path parameter is required", 400)

    report_id = identifier(report_id)
    store.tenant(auth.tenant_id)

    record = store.get(auth.tenant_id, "REPORT#" + report_id)
    if not record:
        return error("report_not_found", "Requested report does not exist in your tenant", 404)

    s3_key = record.get("s3_key") or f"tenants/{auth.tenant_id}/reports/{report_id}.json"
    if s3_key != f"tenants/{auth.tenant_id}/reports/{report_id}.json":
        raise AccessDenied("report_object_not_in_tenant")
    bucket = reports_bucket_name or os.environ.get("REPORTS_BUCKET")

    download_url = None
    full = {}
    if bucket:
        s3 = s3_client or presign_client()
        full = stored_report(s3, bucket, s3_key, report_id)
        try:
            download_url = s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": s3_key},
                ExpiresIn=3600,
            )
        except Exception:
            download_url = None

    return ok({
        "tenant_id": auth.tenant_id,
        "report_id": report_id,
        "period": record.get("period"),
        "status": record.get("status"),
        "ai_status": record.get("ai_status"),
        # The table keeps a 1000-character preview; the stored report has the whole text.
        "summary": full.get("summary") or record.get("summary"),
        "metrics": full.get("metrics") or record.get("metrics", {}),
        "anomalies": full.get("anomalies") or record.get("anomalies", []),
        "stale_sources": full.get("stale_sources", []),
        "created_at": record.get("created_at"),
        "s3_key": s3_key,
        "download_url": download_url,
    })


def handle_post_trigger_report(
    event: dict,
    store: DynamoStore,
    auth: AuthContext,
    sqs_client=None,
    report_queue_url: str | None = None,
) -> dict:
    """Manually trigger daily AI report generation, strictly enforcing 1 report/day quota."""
    if auth.role not in ("admin", "analyst"):
        raise AccessDenied("role_not_allowed")

    now = datetime.now(UTC)
    vietnam_date = now.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date().isoformat()

    # Acquire daily report lock atomically using DynamoDB Conditional Write (T#<tenant>, sk=LOCK#YYYYMMDD).
    # Prevents race conditions and duplicate AI calls from concurrent triggers or schedulers.
    job_id = stable_id(auth.tenant_id, "report", vietnam_date, str(uuid.uuid4())[:6])
    acquired = store.acquire_report_lock(
        auth.tenant_id,
        vietnam_date,
        job_id=job_id,
        triggered_by=auth.user_id,
    )
    if not acquired:
        raise QuotaExceeded("daily_ai_report_quota_reached")

    body = {
        "schema_version": 1,
        "tenant_id": auth.tenant_id,
        "source_id": "report",
        "job_id": job_id,
        "kind": "report",
        "scheduled_at": iso(now),
        "manual_trigger": True,
        "triggered_by": auth.user_id,
    }

    # The aggregator marks JOB#<id> completed and fails if it does not exist, same as
    # the scheduled ReportDispatcher creates it before enqueueing.
    store.create_job(
        auth.tenant_id,
        job_id,
        {**body, "status": "queued", "row_count": 0, "expires_at": int((now + timedelta(days=30)).timestamp())},
    )

    queue_url = report_queue_url or os.environ.get("REPORT_QUEUE_URL")
    if queue_url:
        sqs = sqs_client or boto3.client("sqs")
        sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps(body))

    return ok(
        {
            "tenant_id": auth.tenant_id,
            "job_id": job_id,
            "period": vietnam_date,
            "status": "queued",
            "message": "Report generation queued successfully",
        },
        status_code=202,
    )
