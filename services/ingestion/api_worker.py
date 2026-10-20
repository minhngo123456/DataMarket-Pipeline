"""REST API extractor adapter for scheduled jobs."""

import json
import logging
import os
import urllib.error
import urllib.request

import boto3

from marketdata.contracts import AccessDenied, InvalidRecord, identifier
from marketdata.queue import send_rows
from marketdata.repository import DynamoStore

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

REQUIRED = {"source_sku", "price", "currency", "observed_at"}
MAX_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 10


def default_fetcher(url, headers=None, timeout=TIMEOUT_SECONDS):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "MarketDataPipeline/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise InvalidRecord("api_response_exceeds_1_mib")
            return data
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise InvalidRecord("api_rate_limited_429") from exc
        if exc.code in (401, 403):
            raise AccessDenied(f"api_access_denied_{exc.code}") from exc
        raise InvalidRecord(f"api_http_error_{exc.code}") from exc
    except urllib.error.URLError as exc:
        raise InvalidRecord(f"api_connection_error_{exc.reason}") from exc


def parse_api_payload(data):
    if len(data) > MAX_BYTES:
        raise InvalidRecord("api_response_exceeds_1_mib")
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidRecord("invalid_json_payload") from exc

    if isinstance(payload, dict):
        # Allow wrapper keys like {"items": [...]}, {"products": [...]}, {"data": [...]}
        for key in ("items", "products", "data", "prices"):
            if isinstance(payload.get(key), list):
                items = payload[key]
                break
        else:
            raise InvalidRecord("api_payload_missing_items_list")
    elif isinstance(payload, list):
        items = payload
    else:
        raise InvalidRecord("api_payload_must_be_list_or_dict")

    if not items:
        raise InvalidRecord("empty_api_response")
    if len(items) > 1000:
        raise InvalidRecord("api_exceeds_1000_rows")

    rows = []
    for item in items:
        if not isinstance(item, dict):
            raise InvalidRecord("invalid_item_format")
        if not REQUIRED <= set(item.keys()):
            raise InvalidRecord("missing_required_api_fields")
        if "tenant_id" in item:
            raise InvalidRecord("tenant_not_allowed_in_record")

        # Clean string representations
        row = {
            "source_sku": str(item["source_sku"]).strip(),
            "price": str(item["price"]).strip(),
            "currency": str(item["currency"]).strip().upper(),
            "observed_at": str(item["observed_at"]).strip(),
        }
        if "price_basis" in item:
            row["price_basis"] = str(item["price_basis"]).strip()
        rows.append(row)

    return rows


def process_event(event, store, sqs, transform_queue, fetcher=None):
    fetch = fetcher or default_fetcher

    if event.get("schema_version") != 1:
        raise InvalidRecord("unsupported_schema_version")
    if event.get("kind") != "api":
        raise AccessDenied("api_job_required")

    tenant_id = identifier(event["tenant_id"])
    source_id = identifier(event["source_id"])
    job_id = identifier(event["job_id"])

    job = store.get(tenant_id, "JOB#" + job_id)
    if not job:
        raise AccessDenied("job_not_found")
    store.require_job({"tenant_id": tenant_id, "source_id": source_id, "job_id": job_id})
    if job.get("kind") != "api":
        raise AccessDenied("api_job_required")

    source = store.get(tenant_id, "SOURCE#" + source_id)
    if not source or not source.get("enabled"):
        store.update_job(tenant_id, job_id, status="failed", error="source_disabled_or_missing", row_count=0)
        return

    endpoint = source.get("endpoint") or source.get("url")
    if not endpoint:
        store.update_job(tenant_id, job_id, status="failed", error="missing_api_endpoint", row_count=0)
        return

    try:
        raw_data = fetch(endpoint, headers=source.get("headers"))
        rows = parse_api_payload(raw_data)
    except (InvalidRecord, AccessDenied) as exc:
        store.update_job(tenant_id, job_id, status="failed", error=str(exc), row_count=0)
        return
    except Exception as exc:
        store.update_job(tenant_id, job_id, status="failed", error=f"fetch_failed_{exc}", row_count=0)
        return

    values = [
        {
            "schema_version": 1,
            "tenant_id": tenant_id,
            "source_id": source_id,
            "job_id": job_id,
            "row_number": i,
            "record": row,
        }
        for i, row in enumerate(rows, 1)
    ]

    # Validate size limits (32 KiB per row)
    if any(len(json.dumps(v, ensure_ascii=False).encode()) > 32 * 1024 for v in values):
        store.update_job(
            tenant_id, job_id, status="failed", error="row_exceeds_32_kib", row_count=0
        )
        return

    store.update_job(tenant_id, job_id, row_count=len(rows), status="normalizing")
    send_rows(sqs, transform_queue, values)


def process_batch(event, store, sqs, queue, fetcher=None):
    failures = []
    for message in event["Records"]:
        try:
            body = json.loads(message["body"])
            process_event(body, store, sqs, queue, fetcher=fetcher)
        except Exception:
            logger.error("api_failed message_id=%s", message["messageId"], exc_info=True)
            failures.append({"itemIdentifier": message["messageId"]})
    return {"batchItemFailures": failures}


def handler(event, context):
    return process_batch(
        event,
        DynamoStore(),
        boto3.client("sqs"),
        os.environ["TRANSFORM_QUEUE_URL"],
    )
