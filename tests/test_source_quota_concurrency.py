"""Concurrent source catalog mutations must respect each tenant's quota."""

import pytest

from scripts.demo_source_pipeline import local_pipeline


def post_source(pipeline, source_id, *, user="admin-a"):
    return pipeline.api("POST", "/sources", user=user, body={
        "source_id": source_id, "kind": "csv", "enabled": True,
    })


def interleave_catalog_write(pipeline, monkeypatch, source_id, operation, callback):
    transact = pipeline.store.client.transact_write_items
    interleaved = []

    def before_commit(**kwargs):
        if not interleaved and any(
            item.get(operation, {}).get("Key", {}).get("sk", {}).get("S") == "SOURCE#" + source_id
            for item in kwargs["TransactItems"]
        ):
            interleaved.append(True)
            callback()
        return transact(**kwargs)

    monkeypatch.setattr(pipeline.store.client, "transact_write_items", before_commit)
    return interleaved


@pytest.mark.parametrize("legacy", [False, True])
def test_two_creations_competing_for_last_slot_cannot_stop_dispatch(legacy, monkeypatch):
    with local_pipeline() as p:
        p.configure_sources()
        if legacy:
            p.store.control.update_item(
                Key={"pk": "T#tenant-a", "sk": "META"},
                UpdateExpression="REMOVE source_catalog_revision",
            )
        concurrent_statuses = []
        interleaved = interleave_catalog_write(
            p, monkeypatch, "outer-source", "Update",
            lambda: concurrent_statuses.append(post_source(p, "inner-source")[0]),
        )

        status, response = post_source(p, "outer-source")

        assert interleaved == [True]
        assert concurrent_statuses == [201]
        assert status == 429
        assert response["error"]["code"] == "quota_exceeded"
        assert len(list(p.store.sources("tenant-a"))) == 3
        assert p.store.get("tenant-a", "SOURCE#outer-source") is None
        assert p.store.get("tenant-a", "SOURCE#inner-source") is not None
        assert p.dispatch() == {"sent": 2}
        assert list(p.store.sources("tenant-b")) == []


def test_concurrent_delete_and_create_recount_without_losing_a_slot(monkeypatch):
    with local_pipeline() as p:
        p.configure_sources()
        deletions = []
        interleaved = interleave_catalog_write(
            p, monkeypatch, "outer-source", "Update",
            lambda: deletions.append(p.api("DELETE", "/sources/web_competitor")[0]),
        )

        assert post_source(p, "outer-source")[0] == 201

        assert interleaved == [True]
        assert deletions == [200]
        assert {source["source_id"] for source in p.store.sources("tenant-a")} == {
            "api_supplier", "outer-source",
        }
        assert post_source(p, "last-slot")[0] == 201
        assert post_source(p, "over-limit")[0] == 429
        assert p.dispatch() == {"sent": 1}


def test_deleting_distinct_sources_concurrently_keeps_catalog_consistent(monkeypatch):
    with local_pipeline() as p:
        p.configure_sources()
        deletions = []
        interleaved = interleave_catalog_write(
            p, monkeypatch, "api_supplier", "Delete",
            lambda: deletions.append(p.api("DELETE", "/sources/web_competitor")[0]),
        )

        assert p.api("DELETE", "/sources/api_supplier")[0] == 200

        assert interleaved == [True]
        assert deletions == [200]
        assert list(p.store.sources("tenant-a")) == []
        for index in range(3):
            assert post_source(p, "refill-" + str(index))[0] == 201
        assert post_source(p, "fourth")[0] == 429


def test_same_id_creation_conflicts_without_consuming_an_extra_slot(monkeypatch):
    with local_pipeline() as p:
        p.configure_sources()
        creations = []
        interleaved = interleave_catalog_write(
            p, monkeypatch, "same-id", "Update",
            lambda: creations.append(post_source(p, "same-id")[0]),
        )

        status, response = post_source(p, "same-id")

        assert interleaved == [True]
        assert creations == [201]
        assert status == 409
        assert response["error"]["message"] == "source_configuration_changed"
        assert len(list(p.store.sources("tenant-a"))) == 3


def test_quota_reduction_between_count_and_commit_is_enforced(monkeypatch):
    with local_pipeline() as p:
        p.configure_sources()

        def lower_quota():
            p.store.control.update_item(
                Key={"pk": "T#tenant-a", "sk": "META"},
                UpdateExpression="SET max_sources = :limit",
                ExpressionAttributeValues={":limit": 2},
            )

        interleaved = interleave_catalog_write(p, monkeypatch, "outer-source", "Update", lower_quota)

        assert post_source(p, "outer-source")[0] == 429

        assert interleaved == [True]
        assert len(list(p.store.sources("tenant-a"))) == 2
        assert p.store.get("tenant-a", "SOURCE#outer-source") is None


def test_catalog_revisions_are_tenant_scoped_and_configuration_edits_keep_slots():
    with local_pipeline() as p:
        for index in range(3):
            assert post_source(p, "source-" + str(index))[0] == 201
            assert post_source(p, "source-" + str(index), user="admin-b")[0] == 201
        before = p.store.tenant("tenant-a")["source_catalog_revision"]

        assert p.api("PATCH", "/sources/source-0", body={"enabled": False})[0] == 200
        assert post_source(p, "source-0")[0] == 201

        assert p.store.tenant("tenant-a")["source_catalog_revision"] == before
        assert len(list(p.store.sources("tenant-a"))) == 3
        assert len(list(p.store.sources("tenant-b"))) == 3
        assert post_source(p, "source-3")[0] == 429
        assert post_source(p, "source-3", user="admin-b")[0] == 429
