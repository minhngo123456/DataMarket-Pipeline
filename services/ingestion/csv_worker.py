"""CSV reference adapter for the shared contract; API/HTML adapters remain Hướng's work."""

import csv
import io
import json
import logging
import os

import boto3

from marketdata.contracts import (
    MAX_CSV_BYTES,
    MAX_CSV_ROWS,
    AccessDenied,
    InvalidRecord,
    identifier,
)
from marketdata.queue import send_rows
from marketdata.repository import DynamoStore
from services.ingestion.batch import receive_count
from services.ingestion.collection import collection_error_code
from services.ingestion.fetch_errors import MAX_RECEIVE_COUNT

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
REQUIRED = {"source_sku", "price", "currency", "observed_at"}
MAX_BYTES = MAX_CSV_BYTES


def parse_csv(data):
    if len(data) > MAX_BYTES:
        raise InvalidRecord("csv_exceeds_1_mib")
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
        headers = reader.fieldnames or []
        if (
            not REQUIRED <= set(headers)
            or len(headers) != len(set(headers))
            or "tenant_id" in headers
        ):
            raise InvalidRecord("invalid_csv_headers")
        rows = []
        for row in reader:
            if None in row or None in row.values():
                raise InvalidRecord("invalid_csv_columns")
            if len(rows) == MAX_CSV_ROWS:
                raise InvalidRecord("csv_exceeds_1000_rows")
            rows.append(row)
        if not rows:
            raise InvalidRecord("empty_csv")
        return rows
    except (UnicodeDecodeError, csv.Error) as exc:
        raise InvalidRecord("invalid_utf8_csv") from exc


def process_event(event, store, s3, sqs, bucket, transform_queue):
    if event.get("source") != "aws.s3" or event.get("detail-type") != "Object Created":
        raise InvalidRecord("expected_s3_object_created")
    detail = event["detail"]
    key = detail["object"]["key"]
    parts = key.split("/")
    if (
        detail["bucket"]["name"] != bucket
        or len(parts) != 4
        or parts[0] != "tenants"
        or parts[2] != "raw"
    ):
        raise AccessDenied("invalid_upload_location")
    tenant_id, job_id = identifier(parts[1]), identifier(parts[3])
    job = store.get(tenant_id, "JOB#" + job_id)
    if not job or job.get("raw_key") != key:
        raise AccessDenied("upload_job_unavailable")
    if job["kind"] != "csv":
        raise AccessDenied("csv_job_required")
    source = store.source(tenant_id, job["source_id"])
    if source.get("kind") != "csv":
        raise AccessDenied("csv_job_required")
    version = detail["object"].get("version-id")
    if not version:
        raise InvalidRecord("s3_version_required")
    # A second PUT may use the same presigned URL. Only the first bound version is processed.
    store.bind_csv_version(tenant_id, job_id, version)
    if job.get("status") == "failed":
        store.record_source_collection(
            tenant_id, job["source_id"], job_id, "csv", "failed",
            collection_error_code(job.get("error", "csv_worker_error"), "csv"),
        )
        return
    store.require_job({"tenant_id": tenant_id, "source_id": job["source_id"], "job_id": job_id})
    if job.get("status") == "completed":
        store.record_source_collection(tenant_id, job["source_id"], job_id, "csv", "succeeded")
        return
    store.record_source_collection(tenant_id, job["source_id"], job_id, "csv", "running")
    response = s3.get_object(Bucket=bucket, Key=key, VersionId=version)
    if response["ContentLength"] > MAX_BYTES:
        response["Body"].close()
        fail_csv_job(store, tenant_id, job, "csv_exceeds_1_mib")
        return
    try:
        rows = parse_csv(response["Body"].read(MAX_BYTES + 1))
    except InvalidRecord as exc:
        fail_csv_job(store, tenant_id, job, collection_error_code(exc, "csv"))
        return
    finally:
        response["Body"].close()
    values = [
        {
            "schema_version": 1,
            "tenant_id": tenant_id,
            "source_id": job["source_id"],
            "job_id": job_id,
            "row_number": i,
            "record": row,
        }
        for i, row in enumerate(rows, 1)
    ]
    # Validate all message sizes before sending any row.
    if any(len(json.dumps(v, ensure_ascii=False).encode()) > 32 * 1024 for v in values):
        fail_csv_job(store, tenant_id, job, "row_exceeds_32_kib")
        return
    committed_job = store.begin_csv_normalization(
        tenant_id, job_id, job["source_id"], version, len(rows)
    )
    if committed_job["status"] == "completed":
        store.record_source_collection(tenant_id, job["source_id"], job_id, "csv", "succeeded")
        return
    send_rows(sqs, transform_queue, values)
    store.record_source_collection(tenant_id, job["source_id"], job_id, "csv", "succeeded")


def fail_csv_job(store, tenant_id, job, code):
    if store.fail_source_job(tenant_id, job["job_id"], job["source_id"], "csv", code):
        store.record_source_collection(tenant_id, job["source_id"], job["job_id"], "csv", "failed", code)


def record_csv_retry(store, message, bucket):
    """Only record failures for an upload whose trusted object version is bound."""
    try:
        event = json.loads(message["body"])
        if event.get("source") != "aws.s3" or event.get("detail-type") != "Object Created":
            return
        detail = event["detail"]
        key = detail["object"]["key"]
        parts = key.split("/")
        if (
            detail["bucket"]["name"] != bucket or len(parts) != 4
            or parts[0] != "tenants" or parts[2] != "raw"
        ):
            return
        tenant_id, job_id = identifier(parts[1]), identifier(parts[3])
        job = store.get(tenant_id, "JOB#" + job_id)
        if (
            not job or job.get("kind") != "csv" or job.get("raw_key") != key
            or not job.get("raw_version") or job["raw_version"] != detail["object"].get("version-id")
        ):
            return
        if receive_count(message) >= MAX_RECEIVE_COUNT:
            fail_csv_job(store, tenant_id, job, "retry_limit_exceeded_csv_worker_error")
        else:
            store.record_source_collection(
                tenant_id, job["source_id"], job_id, "csv", "retrying", "csv_worker_error"
            )
    except (KeyError, TypeError, ValueError):
        return


def process_batch(event, store, s3, sqs, bucket, queue):
    failures = []
    for message in event["Records"]:
        try:
            process_event(json.loads(message["body"]), store, s3, sqs, bucket, queue)
        except Exception:
            logger.error("csv_failed message_id=%s", message["messageId"])
            try:
                record_csv_retry(store, message, bucket)
            except Exception:
                logger.error("csv_collection_status_update_failed message_id=%s", message["messageId"])
            failures.append({"itemIdentifier": message["messageId"]})
    return {"batchItemFailures": failures}


def handler(event, context):
    return process_batch(
        event,
        DynamoStore(),
        boto3.client("s3"),
        boto3.client("sqs"),
        os.environ["RAW_BUCKET"],
        os.environ["TRANSFORM_QUEUE_URL"],
    )
