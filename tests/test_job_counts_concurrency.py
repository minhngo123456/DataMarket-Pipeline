"""Durable receipt revisions protect count refreshes and SKU reviews in Moto."""

import json
from copy import deepcopy

import pytest

from marketdata.service import ingest
from scripts.demo_source_pipeline import local_pipeline
from services.normalizer.app import process_batch


@pytest.fixture
def pipeline_rows():
    with local_pipeline() as pipeline:
        pipeline.configure_sources()
        assert pipeline.dispatch() == {"sent": 2}
        run = pipeline.run_sources("api")
        assert not run["batchItemFailures"]
        values = [json.loads(message["Body"]) for message in pipeline._receive("transform")]
        assert len(values) == 3
        yield pipeline, run["bodies"][0]["job_id"], values


def batch(values, prefix="counts"):
    return {"Records": [
        {"messageId": f"{prefix}-{index}", "body": json.dumps(value)}
        for index, value in enumerate(values)
    ]}


def stored_job(pipeline, job_id):
    return pipeline.store.get("tenant-a", "JOB#" + job_id)


def review(pipeline, observation_id, action):
    return pipeline.api("POST", "/pending-skus/approve", body={
        "observation_id": observation_id, "product_id": "phone", "action": action,
    })


def test_older_refresh_cannot_reopen_job_completed_by_another_normalizer(pipeline_rows, monkeypatch):
    p, job_id, values = pipeline_rows
    update = p.store.update_job
    nested_states = []

    def finish_other_rows_before_old_write(tenant_id, update_id, **fields):
        if not nested_states and fields.get("counts", {}).get("accepted") == 1:
            nested_states.append("started")
            assert process_batch(batch(values[1:], "parallel"), p.store, p.now) == {
                "batchItemFailures": [],
            }
            nested_states.append(stored_job(p, job_id)["status"])
        return update(tenant_id, update_id, **fields)

    monkeypatch.setattr(p.store, "update_job", finish_other_rows_before_old_write)

    assert process_batch(batch(values[:1], "older"), p.store, p.now) == {"batchItemFailures": []}

    assert nested_states == ["started", "completed"]
    assert len(p.store.receipts("tenant-a", job_id=job_id)) == 3
    assert p.job(job_id)["status"] == "completed"
    assert p.job(job_id)["counts"] == {"accepted": 3, "pending": 0, "rejected": 0}


def test_receipt_commit_without_other_refresh_invalidates_an_older_snapshot(pipeline_rows, monkeypatch):
    p, job_id, values = pipeline_rows
    assert ingest(values[0], p.store, p.now)["status"] == "accepted"
    update = p.store.update_job
    committed = []

    def commit_before_old_write(tenant_id, update_id, **fields):
        if not committed:
            committed.append(True)
            for value in values[1:]:
                assert ingest(value, p.store, p.now)["status"] == "accepted"
        return update(tenant_id, update_id, **fields)

    monkeypatch.setattr(p.store, "update_job", commit_before_old_write)

    counts = p.store.refresh_job("tenant-a", job_id)

    assert counts == {"accepted": 3, "pending": 0, "rejected": 0}
    assert p.job(job_id)["status"] == "completed"
    assert p.job(job_id)["counts"] == counts


def test_job_is_read_before_receipts_even_when_competing_refreshes_remain_partial(pipeline_rows, monkeypatch):
    p, job_id, values = pipeline_rows
    ingest(values[0], p.store, p.now)
    receipts = p.store.receipts
    interleaved = []

    def return_old_rows_after_new_refresh(*args, **kwargs):
        rows = receipts(*args, **kwargs)
        if not interleaved and kwargs.get("job_id") == job_id:
            interleaved.append(True)
            ingest(values[1], p.store, p.now)
            assert p.store.refresh_job("tenant-a", job_id) == {
                "accepted": 2, "pending": 0, "rejected": 0,
            }
        return rows

    monkeypatch.setattr(p.store, "receipts", return_old_rows_after_new_refresh)

    assert p.store.refresh_job("tenant-a", job_id) == {
        "accepted": 2, "pending": 0, "rejected": 0,
    }
    assert p.job(job_id)["status"] == "normalizing"
    assert p.job(job_id)["counts"]["accepted"] == 2


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_review_invalidates_a_concurrent_count_snapshot(pipeline_rows, monkeypatch, action):
    p, job_id, values = pipeline_rows
    values = deepcopy(values)
    values[0]["record"]["source_sku"] = "UNMAPPED-COUNT-FIXTURE"
    assert process_batch(batch(values), p.store, p.now) == {"batchItemFailures": []}
    pending = p.store.receipts("tenant-a", job_id=job_id, status="pending")[0]
    update = p.store.update_job
    reviewed = []

    def review_before_old_write(tenant_id, update_id, **fields):
        if not reviewed and fields.get("counts", {}).get("pending") == 1:
            reviewed.append(True)
            assert review(p, pending["observation_id"], action)[0] == 200
        return update(tenant_id, update_id, **fields)

    monkeypatch.setattr(p.store, "update_job", review_before_old_write)

    counts = p.store.refresh_job("tenant-a", job_id)

    expected = {"accepted": 3, "pending": 0, "rejected": 0}
    if action == "reject":
        expected.update(accepted=2, rejected=1)
    assert reviewed == [True]
    assert counts == expected
    assert p.job(job_id)["counts"] == expected
    assert p.job(job_id)["status"] == "completed"


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_review_retry_repairs_counts_after_commit_before_refresh_failure(pipeline_rows, monkeypatch, action):
    p, job_id, values = pipeline_rows
    values = deepcopy(values)
    values[0]["record"]["source_sku"] = "UNMAPPED-RETRY-FIXTURE"
    assert process_batch(batch(values), p.store, p.now) == {"batchItemFailures": []}
    pending = p.store.receipts("tenant-a", job_id=job_id, status="pending")[0]
    revision = stored_job(p, job_id)["receipts_revision"]
    refresh = p.store.refresh_job

    def interrupted_refresh(*args, **kwargs):
        raise RuntimeError("local-injected-refresh-failure")

    monkeypatch.setattr(p.store, "refresh_job", interrupted_refresh)
    assert review(p, pending["observation_id"], action)[0] == 500
    assert stored_job(p, job_id)["receipts_revision"] == revision + 1
    assert p.job(job_id)["counts"]["pending"] == 1

    monkeypatch.setattr(p.store, "refresh_job", refresh)
    assert review(p, pending["observation_id"], action)[0] == 200

    assert stored_job(p, job_id)["receipts_revision"] == revision + 1
    assert p.job(job_id)["counts"]["pending"] == 0
    assert p.job(job_id)["counts"]["accepted" if action == "approve" else "rejected"] == (
        3 if action == "approve" else 1
    )


@pytest.mark.parametrize("outcome", ["accepted", "pending", "rejected"])
def test_only_a_successful_new_receipt_increments_job_revision(pipeline_rows, outcome):
    p, job_id, values = pipeline_rows
    value = deepcopy(values[0])
    if outcome == "pending":
        value["record"]["source_sku"] = "UNMAPPED-REVISION-FIXTURE"
    elif outcome == "rejected":
        value["record"]["price"] = "invalid-local-price"
    assert "receipts_revision" not in stored_job(p, job_id)

    assert ingest(value, p.store, p.now)["status"] == outcome
    assert stored_job(p, job_id)["receipts_revision"] == 1
    assert ingest(value, p.store, p.now)["duplicate"] is True
    assert stored_job(p, job_id)["receipts_revision"] == 1
    assert len(p.store.receipts("tenant-a", job_id=job_id)) == 1


@pytest.mark.parametrize("terminal", ["failed", "cancelled"])
def test_refresh_does_not_overwrite_a_concurrent_terminal_job(pipeline_rows, monkeypatch, terminal):
    p, job_id, values = pipeline_rows
    ingest(values[0], p.store, p.now)
    update = p.store.update_job
    changed = []

    def stop_job_before_old_write(tenant_id, update_id, **fields):
        if not changed:
            changed.append(True)
            update(tenant_id, update_id, status=terminal)
        return update(tenant_id, update_id, **fields)

    monkeypatch.setattr(p.store, "update_job", stop_job_before_old_write)

    assert p.store.refresh_job("tenant-a", job_id) == {
        "accepted": 1, "pending": 0, "rejected": 0,
    }
    assert p.job(job_id)["status"] == terminal


def test_refresh_retries_when_extractor_changes_row_count(pipeline_rows, monkeypatch):
    p, job_id, values = pipeline_rows
    for value in values:
        ingest(value, p.store, p.now)
    update = p.store.update_job
    changed = []

    def change_row_count_before_old_write(tenant_id, update_id, **fields):
        if not changed:
            changed.append(True)
            update(tenant_id, update_id, row_count=4)
        return update(tenant_id, update_id, **fields)

    monkeypatch.setattr(p.store, "update_job", change_row_count_before_old_write)

    assert p.store.refresh_job("tenant-a", job_id)["accepted"] == 3
    assert p.job(job_id)["row_count"] == 4
    assert p.job(job_id)["status"] == "normalizing"


def test_legacy_job_without_revision_can_refresh_and_accept_new_receipts(pipeline_rows):
    p, job_id, values = pipeline_rows
    ingest(values[0], p.store, p.now)
    p.store.control.update_item(
        Key={"pk": "T#tenant-a", "sk": "JOB#" + job_id},
        UpdateExpression="REMOVE receipts_revision",
    )

    assert p.store.refresh_job("tenant-a", job_id)["accepted"] == 1
    ingest(values[1], p.store, p.now)

    assert stored_job(p, job_id)["receipts_revision"] == 1
    assert p.store.refresh_job("tenant-a", job_id)["accepted"] == 2
    assert p.job(job_id)["counts"]["accepted"] == 2
