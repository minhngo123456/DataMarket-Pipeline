"""CSV reference adapter for the shared contract; API/HTML adapters remain Hướng's work."""

import csv
import io
import json
import logging
import os

import boto3

from marketdata.contracts import AccessDenied, InvalidRecord, identifier
from marketdata.queue import send_rows
from marketdata.repository import DynamoStore

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
REQUIRED = {"source_sku", "price", "currency", "observed_at"}
MAX_BYTES = 1024 * 1024


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
            if len(rows) == 1000:
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
    store.require_job({"tenant_id": tenant_id, "source_id": job["source_id"], "job_id": job_id})
    if job["kind"] != "csv":
        raise AccessDenied("csv_job_required")
    version = detail["object"].get("version-id")
    if not version:
        raise InvalidRecord("s3_version_required")
    # A second PUT may use the same presigned URL. Only the first bound version is processed.
    store.bind_csv_version(tenant_id, job_id, version)
    response = s3.get_object(Bucket=bucket, Key=key, VersionId=version)
    if response["ContentLength"] > MAX_BYTES:
        response["Body"].close()
        store.update_job(tenant_id, job_id, status="failed", error="csv_exceeds_1_mib", row_count=0)
        return
    try:
        rows = parse_csv(response["Body"].read(MAX_BYTES + 1))
    except InvalidRecord as exc:
        store.update_job(tenant_id, job_id, status="failed", error=str(exc), row_count=0)
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
        store.update_job(
            tenant_id, job_id, status="failed", error="row_exceeds_32_kib", row_count=0
        )
        return
    store.update_job(tenant_id, job_id, row_count=len(rows), status="normalizing")
    send_rows(sqs, transform_queue, values)


def process_batch(event, store, s3, sqs, bucket, queue):
    failures = []
    for message in event["Records"]:
        try:
            process_event(json.loads(message["body"]), store, s3, sqs, bucket, queue)
        except Exception:
            logger.error("csv_failed message_id=%s", message["messageId"])
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
