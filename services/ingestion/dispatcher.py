"""Expand trusted Scheduler invocations into one job per enabled source/tenant."""

import json
import os
from datetime import timedelta
from zoneinfo import ZoneInfo

import boto3

from marketdata.contracts import InvalidRecord, iso, stable_id, timestamp
from marketdata.repository import DynamoStore


def dispatch(event, store, sqs, queues):
    scheduled = timestamp(event.get("scheduled_at"))
    mode = event.get("mode")
    if mode not in ("sources", "reports"):
        raise InvalidRecord("invalid_dispatch_mode")
    # Scheduled time is supplied by Scheduler, not current execution time on a retry.
    slot = scheduled.replace(minute=0, second=0, microsecond=0)
    slot = slot.replace(hour=(slot.hour // 6) * 6)
    sent, invalid_tenants = 0, []
    for tenant in store.active_tenants():
        tenant_id = tenant["pk"][2:]
        if mode == "reports":
            period = scheduled.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date().isoformat()
            sources = [{"source_id": "report", "kind": "report"}]
        else:
            period = iso(slot)
            enabled = [s for s in store.sources(tenant_id) if s.get("enabled")]
            if len(enabled) > min(int(tenant.get("max_sources", 3)), 3):
                invalid_tenants.append(tenant_id)
                continue
            sources = [s for s in enabled if s["kind"] in ("api", "web")]
        for source in sources:
            source_id = source.get("source_id") or source["sk"].removeprefix("SOURCE#")
            job_id = stable_id(tenant_id, source_id, period)
            body = {
                "schema_version": 1,
                "tenant_id": tenant_id,
                "source_id": source_id,
                "job_id": job_id,
                "kind": source["kind"],
                "scheduled_at": iso(scheduled),
            }
            fields = {
                **body,
                "status": "queued",
                "row_count": 0,
                "expires_at": int((scheduled + timedelta(days=30)).timestamp()),
            }
            store.create_job(tenant_id, job_id, fields)
            # Always send, even if the job already exists. A prior run may have crashed after PutItem.
            # Standard SQS may duplicate; downstream workers must use the stable job/row IDs.
            sqs.send_message(QueueUrl=queues[source["kind"]], MessageBody=json.dumps(body))
            sent += 1
    if invalid_tenants:
        raise RuntimeError("source_quota_configuration_invalid")
    return {"sent": sent}


def handler(event, context):
    if event.get("mode") != os.environ["DISPATCH_MODE"]:
        raise InvalidRecord("dispatch_mode_not_allowed")
    return dispatch(
        event,
        DynamoStore(),
        boto3.client("sqs"),
        {
            "api": os.environ["API_QUEUE_URL"],
            "web": os.environ["WEB_QUEUE_URL"],
            "report": os.environ["REPORT_QUEUE_URL"],
        },
    )
