"""Obsolete normalization deliveries terminate their own job without touching replacements."""

import json
from datetime import timedelta

import pytest

from marketdata.contracts import iso
from marketdata.service import ingest
from scripts.demo_source_pipeline import local_pipeline
from services.ingestion.csv_worker import process_event as csv_event
from services.normalizer.app import process_batch
from tests.test_source_lifecycle import recreate_source, stored_source, transform_values


def batch(values):
    return {"Records": [
        {"messageId": f"obsolete-row-{index}", "body": json.dumps(value)}
        for index, value in enumerate(values)
    ]}


def collected_rows(pipeline, kind="api"):
    pipeline.configure_sources()
    assert pipeline.dispatch() == {"sent": 2}
    result = pipeline.run_sources(kind)
    assert not result["batchItemFailures"]
    values = transform_values(pipeline)
    assert len(values) == 3
    return values[0]["job_id"], values


@pytest.mark.parametrize("kind", ["api", "web"])
@pytest.mark.parametrize("accepted", [0, 1])
def test_obsolete_collected_job_fails_with_its_durable_counts(kind, accepted):
    with local_pipeline() as pipeline:
        job_id, values = collected_rows(pipeline, kind)
        for value in values[:accepted]:
            # Leave the count cache stale, as if the earlier worker stopped
            # between committing its receipt and refreshing the job.
            assert ingest(value, pipeline.store, pipeline.now)["status"] == "accepted"
        original = pipeline.store.get("tenant-a", "JOB#" + job_id)
        assert original["status"] == "normalizing"
        assert "collection_succeeded_at" in original
        replacement = recreate_source(pipeline, kind)

        assert process_batch(batch(values), pipeline.store, pipeline.now) == {
            "batchItemFailures": [],
        }

        job = pipeline.store.get("tenant-a", "JOB#" + job_id)
        assert job["status"] == "failed"
        assert job["error"] == "source_generation_changed"
        assert job["counts"] == {"accepted": accepted, "pending": 0, "rejected": 0}
        assert job["row_count"] == original["row_count"] == 3
        assert job["collection_succeeded_at"] == original["collection_succeeded_at"]
        assert len(pipeline.store.receipts("tenant-a", job_id=job_id)) == accepted
        assert stored_source(pipeline, kind) == replacement
        assert pipeline.job(job_id)["status"] == "failed"

        # Redelivering immutable obsolete rows must neither reopen the failed
        # job nor create synthetic receipts for data that was never normalized.
        assert process_batch(batch(values), pipeline.store, pipeline.now) == {
            "batchItemFailures": [],
        }
        assert pipeline.store.get("tenant-a", "JOB#" + job_id) == job
        assert len(pipeline.store.receipts("tenant-a", job_id=job_id)) == accepted


@pytest.mark.parametrize("state", ["completed", "failed", "cancelled"])
def test_obsolete_redelivery_preserves_existing_terminal_job(state):
    with local_pipeline() as pipeline:
        job_id, values = collected_rows(pipeline)
        if state == "completed":
            assert process_batch(batch(values), pipeline.store, pipeline.now) == {
                "batchItemFailures": [],
            }
        else:
            pipeline.store.update_job(
                "tenant-a", job_id, status=state, error="existing_terminal_reason",
                counts={"accepted": 0, "pending": 0, "rejected": 0},
            )
        original = pipeline.store.get("tenant-a", "JOB#" + job_id)
        replacement = recreate_source(pipeline)

        assert process_batch(batch(values), pipeline.store, pipeline.now) == {
            "batchItemFailures": [],
        }

        assert pipeline.store.get("tenant-a", "JOB#" + job_id) == original
        assert stored_source(pipeline) == replacement


def test_obsolete_job_finalization_uses_persisted_source_and_tenant():
    with local_pipeline() as pipeline:
        job_id, values = collected_rows(pipeline)
        recreate_source(pipeline)
        original = pipeline.store.get("tenant-a", "JOB#" + job_id)
        unrelated_source = stored_source(pipeline, "web")

        assert not pipeline.store.fail_obsolete_source_job(
            "tenant-a", job_id, "web_competitor",
        )
        assert not pipeline.store.fail_obsolete_source_job("tenant-b", job_id, "api_supplier")
        forged = {**values[0], "source_id": "web_competitor"}
        assert process_batch(batch([forged]), pipeline.store, pipeline.now) == {
            "batchItemFailures": [{"itemIdentifier": "obsolete-row-0"}],
        }

        assert pipeline.store.get("tenant-a", "JOB#" + job_id) == original
        assert pipeline.store.get("tenant-b", "JOB#" + job_id) is None
        assert stored_source(pipeline, "web") == unrelated_source


def test_current_generation_is_not_failed_by_obsolete_job_finalization():
    with local_pipeline() as pipeline:
        job_id, _ = collected_rows(pipeline)
        original = pipeline.store.get("tenant-a", "JOB#" + job_id)

        assert not pipeline.store.fail_obsolete_source_job("tenant-a", job_id, "api_supplier")

        assert pipeline.store.get("tenant-a", "JOB#" + job_id) == original


@pytest.mark.parametrize("state", ["completed", "cancelled"])
def test_obsolete_failure_cannot_overwrite_concurrent_terminal_transition(state, monkeypatch):
    with local_pipeline() as pipeline:
        job_id, values = collected_rows(pipeline)
        accepted = 3 if state == "completed" else 0
        for value in values[:accepted]:
            ingest(value, pipeline.store, pipeline.now)
        recreate_source(pipeline)
        update = pipeline.store.update_job
        concurrent = []

        def finish_before_obsolete_write(tenant_id, update_id, **fields):
            if fields.get("status") == "failed" and not concurrent:
                update(tenant_id, update_id, status=state, counts={
                    "accepted": accepted, "pending": 0, "rejected": 0,
                })
                concurrent.append(pipeline.store.get(tenant_id, "JOB#" + update_id))
            return update(tenant_id, update_id, **fields)

        monkeypatch.setattr(pipeline.store, "update_job", finish_before_obsolete_write)

        assert process_batch(batch(values), pipeline.store, pipeline.now) == {
            "batchItemFailures": [],
        }

        assert len(concurrent) == 1
        assert pipeline.store.get("tenant-a", "JOB#" + job_id) == concurrent[0]
        assert pipeline.job(job_id)["status"] == state


def test_concurrent_pending_rejection_invalidates_obsolete_failure_counts(monkeypatch):
    with local_pipeline() as pipeline:
        job_id, values = collected_rows(pipeline)
        value = {
            **values[0],
            "record": {**values[0]["record"], "source_sku": "UNMAPPED-OBSOLETE-RACE"},
        }
        result = ingest(value, pipeline.store, pipeline.now)
        assert result["status"] == "pending"
        revision = pipeline.store.get("tenant-a", "JOB#" + job_id)["receipts_revision"]
        replacement = recreate_source(pipeline)
        update = pipeline.store.update_job
        snapshots = []

        def reject_before_obsolete_write(tenant_id, update_id, **fields):
            if fields.get("status") == "failed":
                snapshots.append(fields["counts"].copy())
                if len(snapshots) == 1:
                    pipeline.store.reject_pending(
                        tenant_id, result["observation_id"], now=pipeline.now,
                    )
            return update(tenant_id, update_id, **fields)

        monkeypatch.setattr(pipeline.store, "update_job", reject_before_obsolete_write)

        assert process_batch(batch([value]), pipeline.store, pipeline.now) == {
            "batchItemFailures": [],
        }

        assert snapshots == [
            {"accepted": 0, "pending": 1, "rejected": 0},
            {"accepted": 0, "pending": 0, "rejected": 1},
        ]
        job = pipeline.store.get("tenant-a", "JOB#" + job_id)
        assert job["status"] == "failed"
        assert job["error"] == "source_generation_changed"
        assert job["receipts_revision"] == revision + 1
        assert job["counts"] == {"accepted": 0, "pending": 0, "rejected": 1}
        assert pipeline.store.receipt("tenant-a", result["observation_id"])["status"] == "rejected"
        assert stored_source(pipeline) == replacement


def test_obsolete_rows_retry_if_terminal_state_cannot_be_written(monkeypatch):
    with local_pipeline() as pipeline:
        job_id, values = collected_rows(pipeline)
        recreate_source(pipeline)
        original = pipeline.store.get("tenant-a", "JOB#" + job_id)
        finalize = pipeline.store.fail_obsolete_source_job

        def unavailable(*args, **kwargs):
            raise RuntimeError("local_fixture_terminal_write_unavailable")

        monkeypatch.setattr(pipeline.store, "fail_obsolete_source_job", unavailable)
        event = batch(values[:1])

        assert process_batch(event, pipeline.store, pipeline.now) == {
            "batchItemFailures": [{"itemIdentifier": "obsolete-row-0"}],
        }
        assert pipeline.store.get("tenant-a", "JOB#" + job_id) == original

        monkeypatch.setattr(pipeline.store, "fail_obsolete_source_job", finalize)
        assert process_batch(event, pipeline.store, pipeline.now) == {"batchItemFailures": []}
        assert pipeline.job(job_id)["status"] == "failed"
        assert pipeline.store.receipts("tenant-a", job_id=job_id) == []


def test_csv_job_already_collected_fails_when_queued_rows_become_obsolete(monkeypatch):
    with local_pipeline() as pipeline:
        monkeypatch.setenv("RAW_BUCKET", pipeline.bucket)
        source = {"source_id": "csv_normalizer", "kind": "csv", "enabled": True}
        assert pipeline.api("POST", "/sources", body=source)[0] == 201
        status, upload = pipeline.api("POST", "/jobs/csv-upload", body={
            "source_id": source["source_id"],
        })
        assert status == 201
        observed = iso(pipeline.now - timedelta(minutes=15))
        content = (
            "source_sku,price,currency,observed_at,price_basis\n"
            f"001-PHONE,20100000,VND,{observed},tax_included\n"
        ).encode()
        raw = pipeline.s3.put_object(Bucket=pipeline.bucket, Key=upload["s3_key"], Body=content)
        csv_event({
            "source": "aws.s3", "detail-type": "Object Created",
            "detail": {
                "bucket": {"name": pipeline.bucket},
                "object": {"key": upload["s3_key"], "version-id": raw["VersionId"]},
            },
        }, pipeline.store, pipeline.s3, pipeline.sqs, pipeline.bucket, pipeline.queues["transform"])
        values = transform_values(pipeline)
        assert len(values) == 1
        original = pipeline.store.get("tenant-a", "JOB#" + upload["job_id"])
        assert "collection_succeeded_at" in original
        assert pipeline.api("DELETE", "/sources/" + source["source_id"])[0] == 200
        assert pipeline.api("POST", "/sources", body=source)[0] == 201
        replacement = pipeline.store.get("tenant-a", "SOURCE#" + source["source_id"])

        assert process_batch(batch(values), pipeline.store, pipeline.now) == {
            "batchItemFailures": [],
        }

        job = pipeline.store.get("tenant-a", "JOB#" + upload["job_id"])
        assert job["status"] == "failed"
        assert job["error"] == "source_generation_changed"
        assert job["counts"] == {"accepted": 0, "pending": 0, "rejected": 0}
        assert job["collection_succeeded_at"] == original["collection_succeeded_at"]
        assert pipeline.store.get("tenant-a", "SOURCE#" + source["source_id"]) == replacement
        assert pipeline.job(upload["job_id"])["status"] == "failed"
