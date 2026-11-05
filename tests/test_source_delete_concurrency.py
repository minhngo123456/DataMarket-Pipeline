"""Source deletion must preserve a concurrently changed or recreated source."""

import pytest

from marketdata.contracts import iso
from scripts.demo_source_pipeline import local_pipeline

SOURCE_ID = "delete-source"
SOURCE_KEY = "SOURCE#" + SOURCE_ID
SOURCE_PATH = "/sources/" + SOURCE_ID


def create_source(pipeline, *, legacy=False):
    status, _ = pipeline.api("POST", "/sources", body={
        "source_id": SOURCE_ID, "kind": "api", "name": "Original source",
        "endpoint": "https://delete-source.example.invalid/prices",
    })
    assert status == 201
    if legacy:
        pipeline.store.control.update_item(
            Key={"pk": "T#tenant-a", "sk": SOURCE_KEY},
            UpdateExpression="REMOVE source_generation, source_config_revision",
        )
    return pipeline.store.get("tenant-a", SOURCE_KEY)


@pytest.mark.parametrize("legacy", [False, True], ids=["generated", "legacy"])
@pytest.mark.parametrize("change", ["update", "delete", "recreate"])
def test_delete_returns_conflict_if_source_changes_after_its_read(legacy, change, monkeypatch):
    with local_pipeline() as p:
        before = create_source(p, legacy=legacy)
        delete_configuration = p.store.delete_source_configuration
        concurrent_state = []

        def interleaved_delete(tenant_id, source_id, *, previous):
            assert previous == before
            if change == "update":
                status, _ = p.api("PATCH", SOURCE_PATH, body={"name": "Concurrent update"})
                assert status == 200
            else:
                p.store.control.delete_item(Key={"pk": "T#tenant-a", "sk": SOURCE_KEY})
                if change == "recreate":
                    status, _ = p.api("POST", "/sources", body={
                        "source_id": SOURCE_ID, "kind": "api", "name": "Replacement source",
                        "endpoint": "https://replacement.example.invalid/prices",
                    })
                    assert status == 201
            concurrent_state.append(p.store.get("tenant-a", SOURCE_KEY))
            return delete_configuration(tenant_id, source_id, previous=previous)

        monkeypatch.setattr(p.store, "delete_source_configuration", interleaved_delete)

        status, body = p.api("DELETE", SOURCE_PATH)

        assert status == 409
        assert body["error"] == {
            "code": "conflict", "message": "source_configuration_changed",
        }
        assert len(concurrent_state) == 1
        assert p.store.get("tenant-a", SOURCE_KEY) == concurrent_state[0]
        if change == "recreate":
            assert concurrent_state[0]["source_generation"] != before.get("source_generation")


@pytest.mark.parametrize("legacy", [False, True], ids=["generated", "legacy"])
def test_delete_unchanged_source_succeeds_and_repeated_delete_returns_not_found(legacy):
    with local_pipeline() as p:
        create_source(p, legacy=legacy)

        status, body = p.api("DELETE", SOURCE_PATH)

        assert status == 200
        assert body == {"source_id": SOURCE_ID, "status": "deleted"}
        assert p.store.get("tenant-a", SOURCE_KEY) is None
        assert p.api("DELETE", SOURCE_PATH)[0] == 404


@pytest.mark.parametrize("legacy", [False, True], ids=["generated", "legacy"])
def test_worker_health_write_does_not_conflict_with_source_delete(legacy, monkeypatch):
    with local_pipeline() as p:
        before = create_source(p, legacy=legacy)
        p.store.create_job("tenant-a", "delete-health-job", {
            "source_id": SOURCE_ID, "kind": "api", "status": "queued", "row_count": 0,
            "scheduled_at": iso(p.now),
        })
        delete_configuration = p.store.delete_source_configuration
        health_updates = []

        def interleaved_health(tenant_id, source_id, *, previous):
            assert previous == before
            assert p.store.record_source_collection(
                tenant_id, source_id, "delete-health-job", "api", "running", now=p.now,
            )
            health_updates.append(p.store.get(tenant_id, SOURCE_KEY))
            return delete_configuration(tenant_id, source_id, previous=previous)

        monkeypatch.setattr(p.store, "delete_source_configuration", interleaved_health)

        status, _ = p.api("DELETE", SOURCE_PATH)

        assert status == 200
        assert len(health_updates) == 1
        assert health_updates[0]["collection_status"] == "running"
        assert health_updates[0].get("source_config_revision") == before.get("source_config_revision")
        assert p.store.get("tenant-a", SOURCE_KEY) is None
