"""Source health API contracts using real handlers and Moto, without network IO."""

import json
from datetime import timedelta
from uuid import UUID

import pytest

from marketdata.contracts import iso
from scripts.demo_source_pipeline import local_pipeline
from services.ingestion.fetch_errors import RetryableFetchError
from tests.test_source_pipeline import PartialTransformQueue

PUBLIC_HEALTH = {
    "collection_status", "last_job_id", "last_attempt_at", "last_success_at",
    "last_success_job_id", "last_success_job_at", "last_error_code",
}
PRIVATE_FIELDS = {
    "pk", "sk", "tenant_id", "collection_job_key", "collection_job_at",
    "collection_success_key", "source_config_revision", "worker_private_detail",
}


def listed_source(pipeline, source_id="api_supplier", user="admin-a", query=None):
    status, body = pipeline.api("GET", "/sources", user=user, query=query)
    assert status == 200
    assert body["sources"] == body["data"]
    return next(item for item in body["data"] if item["source_id"] == source_id)


def health_fields(source):
    return {name: source[name] for name in PUBLIC_HEALTH if name in source}


def create_health_job(pipeline, *, tenant="tenant-a", job_id="source-health-api-job"):
    pipeline.store.create_job(tenant, job_id, {
        "source_id": "api_supplier", "kind": "api", "status": "normalizing", "row_count": 3,
        "scheduled_at": iso(pipeline.now),
    })
    pipeline.store.record_source_collection(
        tenant, "api_supplier", job_id, "api", "running", now=pipeline.now,
    )
    return job_id


@pytest.mark.parametrize("kind", ["api", "web"])
def test_get_sources_observes_actual_worker_running_retry_and_enqueue_success(kind):
    with local_pipeline() as p:
        configured = p.configure_sources()[kind]
        source_id = configured["source_id"]
        url = configured.get("endpoint") or configured["url"]
        original_feed = p.feeds[url]
        assert not health_fields(listed_source(p, source_id))
        assert p.dispatch() == {"sent": 2}
        running_observations = []

        def rate_limited_fixture(_url, headers=None):
            running = listed_source(p, source_id)
            assert running["collection_status"] == "running"
            assert running["last_job_id"]
            assert running["last_attempt_at"]
            assert "last_error_code" not in running
            running_observations.append(health_fields(running))
            raise RetryableFetchError(f"{kind}_http_retryable_429", retry_after_seconds=1)

        p.feeds[url] = rate_limited_fixture
        first = p.run_sources(kind)
        assert len(first["batchItemFailures"]) == 1
        assert len(running_observations) == 1
        retrying = listed_source(p, source_id)
        assert retrying["collection_status"] == "retrying"
        assert retrying["last_error_code"] == f"{kind}_http_retryable_429"
        assert retrying["last_job_id"] == first["bodies"][0]["job_id"]
        assert "last_success_at" not in retrying

        p.feeds[url] = original_feed
        p.release_retries(kind)
        assert not p.run_sources(kind)["batchItemFailures"]
        succeeded = listed_source(p, source_id)
        assert succeeded["collection_status"] == "succeeded"
        assert succeeded["last_attempt_at"] == retrying["last_attempt_at"]
        assert succeeded["last_success_job_id"] == retrying["last_job_id"]
        assert succeeded["last_success_job_at"] == iso(p.now)
        assert succeeded["last_success_at"]
        assert "last_error_code" not in succeeded
        assert p.job(succeeded["last_job_id"])["status"] == "normalizing"
        assert not PRIVATE_FIELDS.intersection(succeeded)
        assert p.normalize() == 3

        p.advance(hours=6)
        p.feeds[url] = RetryableFetchError(f"{kind}_http_retryable_429")
        p.dispatch()
        assert len(p.run_sources(kind)["batchItemFailures"]) == 1
        next_retry = listed_source(p, source_id)
        assert next_retry["collection_status"] == "retrying"
        assert next_retry["last_job_id"] != succeeded["last_job_id"]
        for name in ("last_success_at", "last_success_job_id", "last_success_job_at"):
            assert next_retry[name] == succeeded[name]


def test_get_sources_only_exposes_public_health_and_configuration_fields():
    with local_pipeline() as p:
        p.configure_sources()
        job_id = create_health_job(p)
        p.store.record_source_collection(
            "tenant-a", "api_supplier", job_id, "api", "succeeded",
            now=p.now + timedelta(seconds=1),
        )
        stored = p.store.get("tenant-a", "SOURCE#api_supplier")
        p.store.put("tenant-a", "SOURCE#api_supplier", {
            **stored, "worker_private_detail": "fixture-internal-detail",
        })

        public = listed_source(p)

        assert health_fields(public) == health_fields(stored)
        assert not PRIVATE_FIELDS.intersection(public)
        assert set(public) <= PUBLIC_HEALTH | {
            "source_id", "kind", "enabled", "name", "updated_at", "endpoint", "url", "headers",
        }


@pytest.mark.parametrize("method", ["POST", "PATCH"])
def test_configuration_write_preserves_health_written_after_its_source_read(method, monkeypatch):
    with local_pipeline() as p:
        configured = p.configure_sources()["api"]
        job_id = create_health_job(p)
        update_configuration = p.store.update_source_configuration
        worker_state = []

        def interleaved_write(*args, **kwargs):
            if not worker_state:
                p.store.record_source_collection(
                    "tenant-a", "api_supplier", job_id, "api", "succeeded",
                    now=p.now + timedelta(seconds=1),
                )
                worker_state.append(p.store.get("tenant-a", "SOURCE#api_supplier"))
            return update_configuration(*args, **kwargs)

        monkeypatch.setattr(p.store, "update_source_configuration", interleaved_write)
        payload = {
            "name": "Updated source label",
            "headers": {"X-Local-Fixture": "***", "X-Unknown-Fixture": "***"},
        }
        path = "/sources/api_supplier"
        if method == "POST":
            path = "/sources"
            payload.update(source_id="api_supplier", kind="api", endpoint=configured["endpoint"])

        status, _ = p.api(method, path, user="analyst-a", body=payload)

        assert status == (201 if method == "POST" else 200)
        assert len(worker_state) == 1
        stored = p.store.get("tenant-a", "SOURCE#api_supplier")
        for name, value in worker_state[0].items():
            if name in PUBLIC_HEALTH or name.startswith("collection_"):
                assert stored[name] == value
        assert stored["name"] == "Updated source label"
        assert stored["headers"] == configured["headers"]
        assert health_fields(listed_source(p)) == health_fields(worker_state[0])


@pytest.mark.parametrize("method", ["POST", "PATCH"])
def test_client_cannot_write_worker_health_tenant_or_configuration_revision(method):
    with local_pipeline() as p:
        p.configure_sources()
        if method == "PATCH":
            create_health_job(p)
        source_id = "client-source" if method == "POST" else "api_supplier"
        before = p.store.get("tenant-a", "SOURCE#" + source_id)
        spoofed = {
            **dict.fromkeys(PUBLIC_HEALTH, "client-supplied-health"),
            **dict.fromkeys(PRIVATE_FIELDS, "client-supplied-internal"),
            "collection_status": "failed", "source_id": source_id, "kind": "api",
            "endpoint": "https://client-config.example.invalid/prices",
            "name": "Allowed configuration", "tenant_id": "tenant-b",
        }
        status, _ = p.api(
            method, "/sources" if method == "POST" else "/sources/" + source_id,
            body=spoofed,
        )

        assert status == (201 if method == "POST" else 200)
        stored = p.store.get("tenant-a", "SOURCE#" + source_id)
        assert stored["name"] == "Allowed configuration"
        assert health_fields(stored) == health_fields(before or {})
        assert stored.get("source_config_revision") != "client-supplied-internal"
        assert stored["pk"] == "T#tenant-a"
        assert stored["sk"] == "SOURCE#" + source_id
        for name in PRIVATE_FIELDS - {"pk", "sk", "source_config_revision"}:
            assert stored.get(name) != spoofed[name]
        assert p.store.get("tenant-b", "SOURCE#" + source_id) is None


def test_changing_existing_source_kind_is_a_conflict_and_preserves_configuration_and_health():
    with local_pipeline() as p:
        p.configure_sources()
        create_health_job(p)
        before = p.store.get("tenant-a", "SOURCE#api_supplier")

        status, response = p.api("POST", "/sources", body={
            "source_id": "api_supplier", "kind": "web",
            "url": "https://changed-kind.example.invalid/prices",
        })

        assert status == 409
        assert response["error"]["code"] == "conflict"
        assert response["error"]["message"] == "source_kind_conflict"
        assert p.store.get("tenant-a", "SOURCE#api_supplier") == before


def test_patching_a_missing_source_returns_not_found_without_creating_it():
    with local_pipeline() as p:
        status, response = p.api("PATCH", "/sources/missing", body={"enabled": True})
        assert status == 404
        assert response["error"]["code"] == "source_not_found"
        assert p.store.get("tenant-a", "SOURCE#missing") is None


@pytest.mark.parametrize("legacy", [False, True], ids=["revision", "legacy"])
@pytest.mark.parametrize("concurrent_change", ["delete", "delete_and_recreate", "update"])
def test_stale_patch_cannot_resurrect_or_overwrite_a_concurrent_configuration(
    concurrent_change, legacy, monkeypatch
):
    with local_pipeline() as p:
        source = p.configure_sources()["api"]
        if legacy:
            p.store.control.update_item(
                Key={"pk": "T#tenant-a", "sk": "SOURCE#api_supplier"},
                UpdateExpression="REMOVE source_config_revision",
            )
        update_configuration = p.store.update_source_configuration
        winners = []

        def interleaved_write(*args, **kwargs):
            if not winners:
                winners.append(None)
                if concurrent_change in ("delete", "delete_and_recreate"):
                    assert p.api("DELETE", "/sources/api_supplier")[0] == 200
                if concurrent_change == "delete_and_recreate":
                    assert p.api("POST", "/sources", body={
                        "source_id": "api_supplier", "kind": "api",
                        "endpoint": source["endpoint"], "name": "Concurrent replacement",
                    })[0] == 201
                elif concurrent_change == "update":
                    assert p.api("PATCH", "/sources/api_supplier", body={
                        "name": "Concurrent winner",
                    })[0] == 200
                winners[0] = p.store.get("tenant-a", "SOURCE#api_supplier")
            return update_configuration(*args, **kwargs)

        monkeypatch.setattr(p.store, "update_source_configuration", interleaved_write)
        status, response = p.api("PATCH", "/sources/api_supplier", body={
            "name": "Stale loser", "headers": None,
        })

        assert status == 409
        assert response["error"] == {
            "code": "conflict", "message": "source_configuration_changed",
        }
        assert p.store.get("tenant-a", "SOURCE#api_supplier") == winners[0]
        if concurrent_change == "delete":
            assert winners[0] is None
        else:
            assert winners[0]["name"].startswith("Concurrent ")


@pytest.mark.parametrize("method", ["POST", "PATCH"])
def test_first_legacy_configuration_edit_initializes_a_revision_and_preserves_health(method):
    with local_pipeline() as p:
        source = p.configure_sources()["api"]
        job_id = create_health_job(p)
        p.store.record_source_collection(
            "tenant-a", "api_supplier", job_id, "api", "succeeded", now=p.now,
        )
        p.store.control.update_item(
            Key={"pk": "T#tenant-a", "sk": "SOURCE#api_supplier"},
            UpdateExpression="REMOVE source_config_revision",
        )
        previous = p.store.get("tenant-a", "SOURCE#api_supplier")
        assert "source_config_revision" not in previous
        path, payload = "/sources/api_supplier", {"name": "First legacy edit"}
        if method == "POST":
            path = "/sources"
            payload.update(
                source_id="api_supplier", kind="api", endpoint=source["endpoint"],
                headers=source["headers"],
            )

        status, _ = p.api(method, path, body=payload)

        assert status == (201 if method == "POST" else 200)
        first = p.store.get("tenant-a", "SOURCE#api_supplier")
        revision = first["source_config_revision"]
        assert UUID(revision).hex == revision
        assert health_fields(first) == health_fields(previous)
        assert "source_config_revision" not in listed_source(p)
        assert p.api("PATCH", "/sources/api_supplier", body={"name": "Second legacy edit"})[0] == 200
        second = p.store.get("tenant-a", "SOURCE#api_supplier")
        assert UUID(second["source_config_revision"]).hex == second["source_config_revision"]
        assert second["source_config_revision"] != revision
        assert health_fields(second) == health_fields(previous)


@pytest.mark.parametrize("initial_state", ["absent", "existing", "legacy"])
def test_post_same_id_conflicts_with_a_concurrent_creation_or_configuration_update(initial_state, monkeypatch):
    with local_pipeline() as p:
        p.configure_sources()
        source_id = "simultaneous-source" if initial_state == "absent" else "api_supplier"
        if initial_state != "absent":
            create_health_job(p)
        if initial_state == "legacy":
            p.store.control.update_item(
                Key={"pk": "T#tenant-a", "sk": "SOURCE#" + source_id},
                UpdateExpression="REMOVE source_config_revision",
            )
        update_configuration = p.store.update_source_configuration
        winners = []

        def interleaved_write(*args, **kwargs):
            if not winners:
                winners.append(None)
                winner_payload = {
                    "name": "Concurrent winner", "headers": {"X-Local-Fixture": "winner-fixture"},
                }
                if initial_state == "absent":
                    winner_payload.update(
                        source_id=source_id, kind="api",
                        endpoint="https://winner.example.invalid/prices",
                    )
                    assert p.api("POST", "/sources", body=winner_payload)[0] == 201
                else:
                    assert p.api("PATCH", "/sources/" + source_id, body=winner_payload)[0] == 200
                winners[0] = p.store.get("tenant-a", "SOURCE#" + source_id)
            return update_configuration(*args, **kwargs)

        monkeypatch.setattr(p.store, "update_source_configuration", interleaved_write)
        status, response = p.api("POST", "/sources", body={
            "source_id": source_id, "kind": "api", "name": "Stale POST loser",
            "endpoint": "https://loser.example.invalid/prices",
            "headers": {"X-Local-Fixture": "***"},
        })

        assert status == 409
        assert response["error"] == {
            "code": "conflict", "message": "source_configuration_changed",
        }
        assert len(winners) == 1
        stored = p.store.get("tenant-a", "SOURCE#" + source_id)
        assert stored == winners[0]
        assert stored["name"] == "Concurrent winner"
        assert stored["headers"] == {"X-Local-Fixture": "winner-fixture"}


@pytest.mark.parametrize("user", ["analyst-a", "viewer-a"])
def test_read_health_keeps_existing_header_masking_and_viewer_write_denial(user):
    with local_pipeline() as p:
        configured = p.configure_sources()["api"]
        create_health_job(p)
        before = p.store.get("tenant-a", "SOURCE#api_supplier")

        public = listed_source(p, user=user)

        assert public["collection_status"] == "running"
        assert public["headers"] == {name: "***" for name in configured["headers"]}
        assert listed_source(p)["headers"] == configured["headers"]
        if user == "viewer-a":
            for method, path, body in (
                ("PATCH", "/sources/api_supplier", {"enabled": False}),
                ("POST", "/sources", {"source_id": "api_supplier", "kind": "api"}),
            ):
                assert p.api(method, path, user=user, body=body)[0] == 403
            assert p.store.get("tenant-a", "SOURCE#api_supplier") == before


def test_header_removal_does_not_remove_collection_history():
    with local_pipeline() as p:
        p.configure_sources()
        job_id = create_health_job(p)
        p.store.record_source_collection(
            "tenant-a", "api_supplier", job_id, "api", "succeeded", now=p.now,
        )
        before = health_fields(listed_source(p))

        assert p.api("PATCH", "/sources/api_supplier", body={"headers": None})[0] == 200

        after = listed_source(p)
        assert "headers" not in after
        assert "headers" not in p.store.get("tenant-a", "SOURCE#api_supplier")
        assert health_fields(after) == before


def test_same_kind_post_replaces_optional_configuration_without_erasing_worker_history():
    with local_pipeline() as p:
        p.configure_sources()
        job_id = create_health_job(p)
        p.store.record_source_collection(
            "tenant-a", "api_supplier", job_id, "api", "succeeded", now=p.now,
        )
        before = health_fields(listed_source(p))

        status, _ = p.api("POST", "/sources", body={
            "source_id": "api_supplier", "kind": "api", "name": "Replacement configuration",
        })

        assert status == 201
        stored = p.store.get("tenant-a", "SOURCE#api_supplier")
        assert stored["name"] == "Replacement configuration"
        assert not {"endpoint", "url", "headers"}.intersection(stored)
        assert health_fields(listed_source(p)) == before


def test_identical_source_and_job_ids_keep_health_and_configuration_inside_each_tenant():
    with local_pipeline() as p:
        p.configure_sources()
        p.configure_sources(user="admin-b")
        for tenant, status in (("tenant-a", "succeeded"), ("tenant-b", "failed")):
            job_id = create_health_job(p, tenant=tenant)
            p.store.record_source_collection(
                tenant, "api_supplier", job_id, "api", status,
                error_code="invalid_json_payload" if status == "failed" else None, now=p.now,
            )
        before_b = p.store.get("tenant-b", "SOURCE#api_supplier")

        a = listed_source(p, user="viewer-a", query={"tenant_id": "tenant-b"})
        b = listed_source(p, user="viewer-b")

        assert a["collection_status"] == "succeeded"
        assert "last_error_code" not in a
        assert b["collection_status"] == "failed"
        assert b["last_error_code"] == "invalid_json_payload"
        assert "last_success_at" not in b
        assert p.api("PATCH", "/sources/api_supplier", body={
            "name": "Tenant A update", "tenant_id": "tenant-b",
        })[0] == 200
        assert p.store.get("tenant-b", "SOURCE#api_supplier") == before_b
        assert listed_source(p)["name"] == "Tenant A update"


@pytest.mark.parametrize("kind", ["api", "web"])
def test_patching_url_and_headers_during_partial_send_keeps_the_bound_snapshot(kind):
    with local_pipeline() as p:
        configured = p.configure_sources()[kind]
        source_id = configured["source_id"]
        original_url = configured.get("endpoint") or configured["url"]
        p.dispatch()
        partial = p.run_sources(kind, output_sqs=PartialTransformQueue(p.sqs))
        assert len(partial["batchItemFailures"]) == 1
        assert p.normalize() == 2
        job_id = partial["bodies"][0]["job_id"]
        before = p.store.get("tenant-a", "JOB#" + job_id)
        assert before["status"] == "normalizing"
        assert before["row_count"] == 3
        assert before["counts"] == {"accepted": 2, "pending": 0, "rejected": 0}
        descriptor = before["source_snapshot"]
        response = p.s3.get_object(
            Bucket=p.bucket, Key=descriptor["key"], VersionId=descriptor["version_id"],
        )
        try:
            original_values = json.loads(response["Body"].read())["values"]
        finally:
            response["Body"].close()
        before_health = health_fields(listed_source(p, source_id))
        assert before_health["collection_status"] == "retrying"
        replacement_url = f"https://updated-{kind}.example.invalid/prices"
        replacement_headers = {"X-Local-Fixture": "new-configuration"}

        def unexpected_fetch(_url, headers=None):
            raise AssertionError("a bound snapshot must not fetch the replacement source")

        p.feeds[replacement_url] = unexpected_fetch
        status, _ = p.api("PATCH", "/sources/" + source_id, body={
            "endpoint" if kind == "api" else "url": replacement_url,
            "headers": replacement_headers,
        })
        assert status == 200
        configured_now = listed_source(p, source_id)
        assert configured_now["endpoint" if kind == "api" else "url"] == replacement_url
        assert configured_now["headers"] == replacement_headers
        assert health_fields(configured_now) == before_health
        p.release_retries(kind)

        retry = p.run_sources(kind)

        assert retry["received"] == 1 and retry["receive_counts"] == [2]
        assert not retry["batchItemFailures"]
        assert p.normalize() == 3
        after = p.store.get("tenant-a", "JOB#" + job_id)
        assert after["source_snapshot"] == descriptor
        assert after["row_count"] == 3
        assert after["status"] == "completed"
        assert after["counts"] == {"accepted": 3, "pending": 0, "rejected": 0}
        assert p.fetch_calls == [{"url": original_url, "headers": configured["headers"]}]
        receipts = p.store.receipts("tenant-a", job_id=job_id)
        assert len(receipts) == 3
        assert {row["source_sku"] for row in receipts} == {
            value["record"]["source_sku"] for value in original_values
        }
        assert all(row["status"] == "accepted" for row in receipts)
        final_health = listed_source(p, source_id)
        assert final_health["collection_status"] == "succeeded"
        assert final_health["last_job_id"] == final_health["last_success_job_id"] == job_id
        assert "last_error_code" not in final_health
