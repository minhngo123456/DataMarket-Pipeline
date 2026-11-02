"""Handler for CSV upload presigned URL generation and job status."""

import json
import os
import uuid
from datetime import UTC, datetime, timedelta

from marketdata.contracts import MAX_CSV_BYTES, AccessDenied, identifier, iso, stable_id
from marketdata.repository import DynamoStore
from services.api.auth import AuthContext
from services.api.response import error, ok
from services.api.s3 import presign_client


def handle_csv_upload_url(
    event: dict,
    store: DynamoStore,
    auth: AuthContext,
    s3_client=None,
    raw_bucket_name: str | None = None,
) -> dict:
    """Generate a presigned S3 PUT URL for tenant CSV upload directly to RawBucket.

    Role: Admin or Analyst only.
    Object key pattern: tenants/{tenant_id}/raw/{job_id}
    """
    if auth.role not in ("admin", "analyst"):
        raise AccessDenied("role_not_allowed")

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return error("invalid_json", "Request body must be valid JSON", 400)

    source_id = body.get("source_id")
    if not source_id:
        return error("missing_source_id", "source_id is required", 400)

    # Optional early check; a presigned PUT cannot cap the size, so CSVWorker still enforces it.
    size_bytes = body.get("size_bytes")
    if size_bytes is not None:
        if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0:
            return error("invalid_size_bytes", "size_bytes must be a non-negative integer", 400)
        if size_bytes > MAX_CSV_BYTES:
            return error("csv_too_large", f"CSV must not exceed {MAX_CSV_BYTES} bytes", 413)

    source_id = identifier(source_id)
    source = store.source(auth.tenant_id, source_id)
    if source.get("kind") != "csv":
        return error("invalid_source_kind", "Upload is only supported for CSV sources", 400)

    now = datetime.now(UTC)
    job_id = stable_id(auth.tenant_id, source_id, now.isoformat(), str(uuid.uuid4())[:8])
    raw_key = f"tenants/{auth.tenant_id}/raw/{job_id}"

    # Create server-side job record in DynamoDB
    store.create_job(
        auth.tenant_id,
        job_id,
        {
            "schema_version": 1,
            "tenant_id": auth.tenant_id,
            "source_id": source_id,
            "job_id": job_id,
            "kind": "csv",
            "status": "queued",
            "raw_key": raw_key,
            "row_count": 0,
            "created_at": iso(now),
            "expires_at": int((now + timedelta(days=30)).timestamp()),
        },
    )

    bucket = raw_bucket_name or os.environ.get("RAW_BUCKET")
    s3 = s3_client or presign_client()

    upload_url = s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket, "Key": raw_key},
        ExpiresIn=900,
    )

    return ok(
        {
            "job_id": job_id,
            "source_id": source_id,
            "s3_key": raw_key,
            "upload_url": upload_url,
            "expires_in_seconds": 900,
            "tenant_id": auth.tenant_id,
        },
        status_code=201,
    )


def handle_get_job_status(event: dict, store: DynamoStore, auth: AuthContext) -> dict:
    """Retrieve current processing status and receipt counts for a job."""
    if auth.role not in ("admin", "analyst"):
        raise AccessDenied("role_not_allowed")

    path_params = event.get("pathParameters") or {}
    job_id = path_params.get("job_id")
    if not job_id:
        return error("missing_job_id", "job_id is required", 400)

    job_id = identifier(job_id)
    store.tenant(auth.tenant_id)

    job = store.get(auth.tenant_id, "JOB#" + job_id)
    if not job:
        return error("job_not_found", "Job does not exist", 404)

    return ok({
        "job_id": job_id,
        "status": job.get("status", "queued"),
        "row_count": int(job.get("row_count", 0)),
        "counts": job.get("counts", {
            "accepted": 0,
            "pending": 0,
            "rejected": 0,
        }),
        "created_at": job.get("created_at"),
    })
