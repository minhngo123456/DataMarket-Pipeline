"""Aggregator Worker consuming ReportQueue.

Extracts the 24-hour price matrix, computes statistical metrics and anomalies,
invokes the Claude API for executive narrative, stores report artifacts in DynamoDB and S3,
and triggers tenant notifications.
"""

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import boto3

from marketdata.contracts import iso, stable_id, timestamp
from marketdata.queries import price_matrix
from marketdata.repository import DynamoStore
from services.reporting.claude_client import ClaudeClient
from services.reporting.notification import dispatch_notification

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def process_report_job(
    message_body: dict,
    store: DynamoStore,
    s3_client=None,
    reports_bucket: str | None = None,
    claude_client=None,
) -> dict:
    """Process a single report job for a tenant."""
    tenant_id = message_body["tenant_id"]
    job_id = message_body["job_id"]
    scheduled = timestamp(message_body["scheduled_at"])

    # Determine reporting date in Asia/Ho_Chi_Minh
    vn_time = scheduled.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
    period = vn_time.date().isoformat()
    report_id = stable_id(tenant_id, period)

    # 1. Fetch 24-hour observation matrix
    matrix = price_matrix(store, tenant_id, now=scheduled, reference_source="internal", basis="tax_included")
    rows = matrix.get("rows", [])

    # 2. Compute statistics and identify anomalies (> 15% price shift)
    products_seen = set()
    anomalies = []
    top_changes = []

    for r in rows:
        products_seen.add(r["product_id"])
        change = r.get("change_percent")
        if change is not None:
            top_changes.append(r)
            if abs(change) >= Decimal("15.00"):
                anomalies.append(r)

    # Check for sources that had no active observations
    all_sources = [s.get("source_id") or s["sk"].removeprefix("SOURCE#") for s in store.sources(tenant_id)]
    active_sources = {r["source_id"] for r in rows}
    stale_sources = [s for s in all_sources if s not in active_sources and s != "report"]

    summary_data = {
        "tenant_id": tenant_id,
        "period": period,
        "window_start": matrix.get("window_start"),
        "window_end": matrix.get("window_end"),
        "stats": {
            "total_products": len(products_seen),
            "total_observations": len(rows),
            "reference_source": "internal",
            "anomaly_count": len(anomalies),
        },
        "anomalies": anomalies,
        "stale_sources": stale_sources,
        "top_changes": top_changes[:5],
    }

    # Check if a completed report already exists for this tenant & period (deduplication & retry safety)
    existing_report = store.get(tenant_id, "REPORT#" + report_id)
    if existing_report and existing_report.get("status") in ("ready", "partial") and existing_report.get("ai_status"):
        logger.info(
            "Report %s already generated for tenant %s (period=%s). Reusing cached result without invoking Claude API.",
            report_id,
            tenant_id,
            period,
        )
        store.update_job(tenant_id, job_id, status="completed", report_id=report_id)
        return {
            "tenant_id": tenant_id,
            "report_id": report_id,
            "status": "completed",
            "reused": True,
        }

    # Record daily lock if not already recorded (e.g. if invoked by scheduled dispatcher)
    store.acquire_report_lock(tenant_id, period, job_id=job_id, triggered_by="aggregator")

    # 3. Call Claude API (reads key from AWS Secrets Manager or env var fallback)
    ai_client = claude_client or ClaudeClient(secret_name=os.environ.get("ANTHROPIC_SECRET_NAME"))
    ai_result = ai_client.generate_report_summary(summary_data)

    # 4. Save full report artifact into S3
    bucket = reports_bucket or os.environ.get("REPORTS_BUCKET")
    s3_key = f"tenants/{tenant_id}/reports/{report_id}.json"

    full_report = {
        "schema_version": 1,
        "tenant_id": tenant_id,
        "report_id": report_id,
        "job_id": job_id,
        "period": period,
        "created_at": iso(datetime.now(UTC)),
        "summary": ai_result.get("text"),
        "ai_status": ai_result.get("status"),
        "metrics": summary_data["stats"],
        "anomalies": anomalies,
        "stale_sources": stale_sources,
        "matrix_sample": rows[:10],
    }

    if bucket:
        s3 = s3_client or boto3.client("s3")
        from services.api.response import decimal_serializer
        s3.put_object(
            Bucket=bucket,
            Key=s3_key,
            Body=json.dumps(full_report, default=decimal_serializer),
            ContentType="application/json",
        )

    # 5. Save metadata / summary in DynamoDB ControlTable
    now_ts = datetime.now(UTC)
    store.put(
        tenant_id,
        "REPORT#" + report_id,
        {
            "report_id": report_id,
            "job_id": job_id,
            "period": period,
            "status": "ready" if ai_result.get("status") != "fallback_quantitative" else "partial",
            "ai_status": ai_result.get("status"),
            "summary": ai_result.get("text")[:1000],  # preview
            "s3_key": s3_key,
            "created_at": iso(now_ts),
            "expires_at": int((now_ts + timedelta(days=90)).timestamp()),
        },
    )

    # Update job status
    store.update_job(tenant_id, job_id, status="completed", report_id=report_id)

    # 6. Trigger tenant notification (SNS / Slack)
    notification_result = dispatch_notification(
        tenant_id=tenant_id,
        report_id=report_id,
        period=period,
        summary_text=ai_result.get("text"),
        store=store,
    )

    return {
        "tenant_id": tenant_id,
        "report_id": report_id,
        "status": "completed",
        "notification": notification_result,
    }


def handler(event: dict, context=None, store: DynamoStore | None = None) -> dict:
    """AWS Lambda entry point for SQS ReportQueue."""
    if store is None:
        store = DynamoStore()

    batch_failures = []
    records = event.get("Records", [])

    for record in records:
        msg_id = record.get("messageId", "unknown")
        try:
            body = json.loads(record.get("body", "{}"))
            process_report_job(body, store)
        except Exception as exc:
            logger.exception("Failed processing report record %s: %s", msg_id, exc)
            batch_failures.append({"itemIdentifier": msg_id})

    return {"batchItemFailures": batch_failures}
