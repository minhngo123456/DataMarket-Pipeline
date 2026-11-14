"""Queue an explicitly requested source collection without fetching inside the API."""

import json
import logging
import os
from datetime import timedelta

import boto3

from marketdata.contracts import AccessDenied, Conflict, InvalidRecord, stable_id, timestamp

logger = logging.getLogger(__name__)


class CollectionUnavailable(Exception):
    def __init__(self, code, *, source_saved=False, job_id=None):
        super().__init__(code)
        self.source_saved = source_saved
        self.job_id = job_id


def collection_queue(body, kind, enabled, url):
    requested = body.get("collect_now", False)
    if type(requested) is not bool:
        raise InvalidRecord("collect_now_must_be_boolean")
    if not requested:
        return None
    if kind not in ("api", "web") or not enabled or not url:
        raise InvalidRecord("collection_requires_enabled_api_or_web_url")
    queue = os.environ.get({"api": "API_QUEUE_URL", "web": "WEB_QUEUE_URL"}[kind])
    if not queue:
        raise CollectionUnavailable("source_collection_not_configured")
    return queue


def enqueue_collection(store, tenant_id, source, queue):
    source_id, kind = source.get("source_id") or source["sk"].removeprefix("SOURCE#"), source["kind"]
    job_id = stable_id(tenant_id, source_id, source.get("source_generation"), source["source_config_revision"], "on-save")
    body = {
        "schema_version": 1, "tenant_id": tenant_id, "source_id": source_id,
        "job_id": job_id, "kind": kind, "scheduled_at": source["updated_at"],
    }
    generation = source.get("source_generation")
    if generation is not None:
        body["source_generation"] = generation
    try:
        store.create_job(tenant_id, job_id, {
            **body, "source_generation": generation, "status": "queued", "row_count": 0,
            "expires_at": int((timestamp(source["updated_at"]) + timedelta(days=30)).timestamp()),
        })
        store.require_job(body)
        store.record_source_collection(tenant_id, source_id, job_id, kind, "queued")
        boto3.client("sqs").send_message(QueueUrl=queue, MessageBody=json.dumps(body))
    except (AccessDenied, Conflict):
        raise
    except Exception as exc:
        # No source URL, headers or provider exception text belongs in diagnostics.
        code = "source_collection_enqueue_failed"
        try:
            if store.fail_source_job(tenant_id, job_id, source_id, kind, code, require_unbound=True):
                store.record_source_collection(tenant_id, source_id, job_id, kind, "failed", code)
        except Exception:
            logger.error("source_collection_failure_status_unavailable")
        raise CollectionUnavailable(code, source_saved=True, job_id=job_id) from exc
    return {"job_id": job_id, "collection_status": "queued"}
