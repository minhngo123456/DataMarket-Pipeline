"""Terminal delivery behavior for obsolete CSV source generations, using Moto."""

import json

from scripts.demo_source_pipeline import local_pipeline
from services.ingestion.csv_worker import process_batch


def test_csv_old_delivery_is_consumed_when_replacement_source_is_disabled(monkeypatch):
    with local_pipeline() as pipeline:
        monkeypatch.setenv("RAW_BUCKET", pipeline.bucket)
        source_id = "csv_lifecycle_edge"
        source = {"source_id": source_id, "kind": "csv", "enabled": True}
        assert pipeline.api("POST", "/sources", body=source)[0] == 201
        status, upload = pipeline.api("POST", "/jobs/csv-upload", body={"source_id": source_id})
        assert status == 201
        old_job = pipeline.store.get("tenant-a", "JOB#" + upload["job_id"])
        raw = pipeline.s3.put_object(
            Bucket=pipeline.bucket, Key=upload["s3_key"], Body=b"fixture-not-read",
        )
        event = {
            "source": "aws.s3", "detail-type": "Object Created",
            "detail": {
                "bucket": {"name": pipeline.bucket},
                "object": {"key": upload["s3_key"], "version-id": raw["VersionId"]},
            },
        }
        assert pipeline.api("DELETE", "/sources/" + source_id)[0] == 200
        assert pipeline.api("POST", "/sources", body={**source, "enabled": False})[0] == 201
        replacement = pipeline.store.get("tenant-a", "SOURCE#" + source_id)
        assert replacement["source_generation"] != old_job["source_generation"]

        class UnexpectedS3:
            def get_object(self, **kwargs):
                raise AssertionError("an obsolete CSV job must not read its upload")

        result = process_batch(
            {"Records": [{"messageId": "obsolete-csv", "body": json.dumps(event)}]},
            pipeline.store, UnexpectedS3(), pipeline.sqs, pipeline.bucket,
            pipeline.queues["transform"],
        )

        assert result == {"batchItemFailures": []}
        assert pipeline.store.get("tenant-a", "SOURCE#" + source_id) == replacement
        failed_job = pipeline.store.get("tenant-a", "JOB#" + upload["job_id"])
        assert failed_job["status"] == "failed"
        assert failed_job["error"] == "source_generation_changed"
        assert "raw_version" not in failed_job
        assert not pipeline._receive("transform")
