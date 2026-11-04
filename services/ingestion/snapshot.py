"""Persist one immutable set of API/Web rows before sending any row to SQS."""

import hashlib
import json
import os
from dataclasses import dataclass

import boto3
from botocore.exceptions import ClientError

from marketdata.contracts import InvalidRecord, envelope, identifier

MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class SourceSnapshot:
    values: list
    descriptor: dict


def snapshot_key(event):
    tenant_id, source_id, job_id = (
        identifier(event[name]) for name in ("tenant_id", "source_id", "job_id")
    )
    # Keep snapshots outside tenants/: that prefix routes S3 events to CSVQueue.
    return f"extractor-snapshots/{tenant_id}/{source_id}/{job_id}.json"


class SourceSnapshots:
    def __init__(self, client=None, bucket=None):
        self.client = client or boto3.client("s3")
        self.bucket = bucket or os.environ["SOURCE_SNAPSHOT_BUCKET"]

    def load(self, event, descriptor=None):
        key = snapshot_key(event)
        if descriptor is not None and (
            not isinstance(descriptor, dict)
            or descriptor.get("key") != key
            or not isinstance(descriptor.get("sha256"), str)
        ):
            raise RuntimeError("source_snapshot_reference_invalid")
        params = {"Bucket": self.bucket, "Key": key}
        if descriptor and descriptor.get("version_id"):
            params["VersionId"] = descriptor["version_id"]
        if descriptor is None:
            # GetObject may return 403 for missing keys without broad ListBucket
            # access. List this exact prefix using the worker's scoped permission.
            objects = self.client.list_objects_v2(Bucket=self.bucket, Prefix=key, MaxKeys=1)
            if not any(item["Key"] == key for item in objects.get("Contents", [])):
                return None
        try:
            response = self.client.get_object(**params)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "NoSuchVersion", "404"):
                # It existed at lookup time, or the job already committed it.
                # Never replace it with a fresh fetch after a deletion/race.
                raise RuntimeError("source_snapshot_missing") from exc
            raise
        try:
            data = response["Body"].read(MAX_SNAPSHOT_BYTES + 1)
        finally:
            response["Body"].close()
        digest = hashlib.sha256(data).hexdigest()
        if len(data) > MAX_SNAPSHOT_BYTES or (descriptor and descriptor["sha256"] != digest):
            raise RuntimeError("source_snapshot_integrity_error")
        try:
            payload = json.loads(data)
            values = payload["values"]
            if (
                payload["schema_version"] != 1
                or payload["kind"] != event["kind"]
                or not isinstance(values, list)
                or not 1 <= len(values) <= 1000
            ):
                raise ValueError("invalid_snapshot_payload")
            for row_number, value in enumerate(values, 1):
                envelope(value)
                if any(value[name] != event[name] for name in ("tenant_id", "source_id", "job_id")):
                    raise ValueError("snapshot_identity_mismatch")
                if value["row_number"] != row_number:
                    raise ValueError("snapshot_row_order_mismatch")
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("source_snapshot_payload_invalid") from exc
        return SourceSnapshot(values, self._descriptor(key, digest, response))

    @staticmethod
    def _descriptor(key, digest, response):
        descriptor = {"key": key, "sha256": digest}
        if response.get("VersionId"):
            descriptor["version_id"] = response["VersionId"]
        return descriptor

    def get_or_create(self, event, job, load_rows):
        descriptor = job.get("source_snapshot")
        # Older workers may already have sent rows. Their original payload cannot
        # safely be reconstructed by fetching the source again after an upgrade.
        if descriptor is None and (job.get("row_count", 0) or job.get("status") == "normalizing"):
            raise RuntimeError("legacy_source_snapshot_unavailable")
        existing = self.load(event, descriptor)
        if existing is not None:
            return existing
        rows = load_rows()
        if not 1 <= len(rows) <= 1000:
            raise InvalidRecord("source_snapshot_invalid_row_count")
        values = [
            {
                "schema_version": 1,
                "tenant_id": event["tenant_id"],
                "source_id": event["source_id"],
                "job_id": event["job_id"],
                "row_number": row_number,
                "record": row,
            }
            for row_number, row in enumerate(rows, 1)
        ]
        # Validate every row before persisting the snapshot or sending anything.
        if any(len(json.dumps(value, ensure_ascii=False).encode()) > 32 * 1024 for value in values):
            raise InvalidRecord("row_exceeds_32_kib")
        payload = {"schema_version": 1, "kind": event["kind"], "values": values}
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        key = snapshot_key(event)
        try:
            response = self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType="application/json",
                IfNoneMatch="*",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "PreconditionFailed":
                raise
            # Another delivery won the conditional write. Both must send its rows.
            winner = self.load(event)
            if winner is None:
                raise RuntimeError("source_snapshot_missing_after_conflict") from exc
            return winner
        return SourceSnapshot(values, self._descriptor(key, hashlib.sha256(data).hexdigest(), response))
