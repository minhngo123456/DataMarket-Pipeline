"""Source deletion/recreation regressions, entirely within Moto's network guard."""

import json
from copy import deepcopy
from datetime import timedelta
from uuid import UUID

import pytest

from marketdata.contracts import AccessDenied, Conflict, iso
from marketdata.service import ingest
from scripts.demo_source_pipeline import local_pipeline
from services.ingestion.csv_worker import process_event as csv_event
from services.normalizer.app import process_batch as normalize_batch


def stored_source(pipeline, kind="api"):
    source_id = {"api": "api_supplier", "web": "web_competitor"}[kind]
    return pipeline.store.get("tenant-a", "SOURCE#" + source_id)


def recreate_source(pipeline, kind="api"):
    previous = stored_source(pipeline, kind)
    source_id = previous["source_id"]
    previous_url = previous.get("endpoint") or previous["url"]
    replacement_url = f"https://replacement-{kind}.example.invalid/prices"
    pipeline.feeds[replacement_url] = pipeline.feeds[previous_url]
    assert pipeline.api("DELETE", "/sources/" + source_id)[0] == 200
    assert pipeline.api("POST", "/sources", body={
        "source_id": source_id, "kind": kind, "name": "Replacement source",
        "endpoint" if kind == "api" else "url": replacement_url,
        "headers": {"X-Local-Fixture": "replacement"}, "enabled": True,
    })[0] == 201
    return stored_source(pipeline, kind)


def queued_event(pipeline, kind="api"):
    messages = pipeline._receive(kind)
    assert len(messages) == 1
    message = messages[0]
    pipeline.sqs.delete_message(
        QueueUrl=pipeline.queues[kind], ReceiptHandle=message["ReceiptHandle"],
    )
    return json.loads(message["Body"])


def enqueue_event(pipeline, kind, event):
    pipeline.sqs.send_message(QueueUrl=pipeline.queues[kind], MessageBody=json.dumps(event))


def transform_values(pipeline):
    messages = pipeline._receive("transform")
    return [json.loads(message["Body"]) for message in messages]


def test_generation_is_server_owned_private_and_stable_across_configuration_edits():
    with local_pipeline() as p:
        payload = {
            "source_id": "private-source", "kind": "api",
            "endpoint": "https://private-source.example.invalid/prices",
            "source_generation": "client-generation", "enabled": True,
        }
        status, created = p.api("POST", "/sources", body=payload)
        assert status == 201
        assert "source_generation" not in created
        first = p.store.get("tenant-a", "SOURCE#private-source")
        generation = first["source_generation"]
        assert UUID(generation).hex == generation
        assert generation != payload["source_generation"]

        for method, path, body in (
            ("PATCH", "/sources/private-source", {
                "name": "Renamed", "source_generation": "forged-patch-generation",
            }),
            ("POST", "/sources", {**payload, "name": "Reconfigured"}),
        ):
            status, response = p.api(method, path, body=body)
            assert status == (200 if method == "PATCH" else 201)
            assert "source_generation" not in response
            current = p.store.get("tenant-a", "SOURCE#private-source")
            assert current["source_generation"] == generation
            assert current["source_config_revision"] != first["source_config_revision"]

        for user in ("admin-a", "analyst-a", "viewer-a"):
            status, response = p.api("GET", "/sources", user=user)
            assert status == 200
            assert all("source_generation" not in item for item in response["data"])

        assert p.api("DELETE", "/sources/private-source")[0] == 200
        assert p.api("POST", "/sources", body=payload)[0] == 201
        replacement = p.store.get("tenant-a", "SOURCE#private-source")
        assert replacement["source_generation"] != generation


@pytest.mark.parametrize("kind", ["api", "web"])
def test_queued_old_job_cannot_fetch_or_update_recreated_source(kind):
    with local_pipeline() as p:
        p.configure_sources()
        assert p.dispatch() == {"sent": 2}
        replacement = recreate_source(p, kind)

        result = p.run_sources(kind)

        assert result["received"] == 1
        assert p.fetch_calls == []
        assert transform_values(p) == []
        assert stored_source(p, kind) == replacement
        assert p.store.receipts("tenant-a") == []


@pytest.mark.parametrize("kind", ["api", "web"])
def test_old_bound_snapshot_is_not_sent_again_or_attached_to_recreated_source(kind):
    with local_pipeline() as p:
        p.configure_sources()
        p.dispatch()
        original = p.run_sources(kind)
        old_event = original["bodies"][0]
        assert len(p.fetch_calls) == 1
        assert len(transform_values(p)) == 3
        replacement = recreate_source(p, kind)
        enqueue_event(p, kind, old_event)

        assert p.run_sources(kind)["received"] == 1

        assert len(p.fetch_calls) == 1
        assert transform_values(p) == []
        assert stored_source(p, kind) == replacement


@pytest.mark.parametrize("kind", ["api", "web"])
def test_recreated_source_gets_a_new_job_in_the_same_schedule_slot(kind):
    with local_pipeline() as p:
        p.configure_sources()
        p.dispatch()
        old_event = queued_event(p, kind)
        replacement = recreate_source(p, kind)

        assert p.dispatch() == {"sent": 2}
        new_event = queued_event(p, kind)

        assert new_event["job_id"] != old_event["job_id"]
        new_job = p.store.get("tenant-a", "JOB#" + new_event["job_id"])
        assert new_job["source_generation"] == replacement["source_generation"]
        enqueue_event(p, kind, new_event)
        completed = p.run_sources(kind)
        assert not completed["batchItemFailures"]
        assert p.normalize() == 3
        assert p.job(new_event["job_id"])["counts"] == {
            "accepted": 3, "pending": 0, "rejected": 0,
        }
        fetches = len(p.fetch_calls)
        enqueue_event(p, kind, new_event)
        assert not p.run_sources(kind)["batchItemFailures"]
        assert p.normalize() == 0
        assert len(p.fetch_calls) == fetches
        assert len(p.store.receipts("tenant-a")) == 3
        assert p.store.receipts("tenant-b") == []


@pytest.mark.parametrize("kind", ["api", "web"])
def test_rows_already_enqueued_by_old_generation_cannot_reach_normalizer(kind):
    with local_pipeline() as p:
        p.configure_sources()
        p.dispatch()
        p.run_sources(kind)
        values = transform_values(p)
        assert len(values) == 3
        replacement = recreate_source(p, kind)

        normalize_batch({"Records": [
            {"messageId": f"old-generation-{index}", "body": json.dumps(value)}
            for index, value in enumerate(values)
        ]}, p.store, p.now)

        assert p.store.receipts("tenant-a") == []
        assert p.matrix()["total"] == 0
        assert stored_source(p, kind) == replacement


@pytest.mark.parametrize("state, health", [("completed", "succeeded"), ("failed", "failed")])
def test_finished_old_job_cannot_fill_current_health_or_success_history(state, health):
    with local_pipeline() as p:
        p.configure_sources()
        job_id = "old-" + state
        p.store.create_job("tenant-a", job_id, {
            "source_id": "api_supplier", "kind": "api", "status": state,
            "scheduled_at": iso(p.now), "row_count": 3,
        })
        replacement = recreate_source(p)

        changed = p.store.record_source_collection(
            "tenant-a", "api_supplier", job_id, "api", health,
            error_code="api_worker_error" if health == "failed" else None,
            now=p.now + timedelta(minutes=1),
        )

        assert not changed
        assert stored_source(p) == replacement


@pytest.mark.parametrize("write", ["current_status", "success_history"])
def test_health_write_checks_generation_atomically_when_source_is_recreated(write, monkeypatch):
    with local_pipeline() as p:
        p.configure_sources()
        p.store.create_job("tenant-a", "health-recreation-race", {
            "source_id": "api_supplier", "kind": "api", "status": "normalizing",
            "scheduled_at": iso(p.now), "row_count": 3,
        })
        update = p.store.control.update_item
        marker = "collection_job_key =" if write == "current_status" else "last_success_at ="
        replacements = []

        def replace_before_source_write(**kwargs):
            if (
                not replacements
                and kwargs["Key"]["sk"] == "SOURCE#api_supplier"
                and marker in kwargs.get("UpdateExpression", "")
            ):
                replacements.append(recreate_source(p))
            return update(**kwargs)

        monkeypatch.setattr(p.store.control, "update_item", replace_before_source_write)

        p.store.record_source_collection(
            "tenant-a", "api_supplier", "health-recreation-race", "api", "succeeded", now=p.now,
        )

        assert len(replacements) == 1
        assert stored_source(p) == replacements[0]


@pytest.mark.parametrize("outcome", ["accepted", "pending", "rejected"])
def test_recreation_between_normalizer_validation_and_write_cannot_commit_a_row(outcome, monkeypatch):
    with local_pipeline() as p:
        p.configure_sources()
        p.dispatch()
        p.run_sources("api")
        value = deepcopy(transform_values(p)[0])
        if outcome == "pending":
            value["record"]["source_sku"] = "UNMAPPED-LIFECYCLE-FIXTURE"
        elif outcome == "rejected":
            value["record"]["price"] = "invalid-fixture-price"
        method = "save_observation" if outcome == "accepted" else "save_receipt"
        save = getattr(p.store, method)
        replacements = []

        def replace_before_commit(*args, **kwargs):
            assert not replacements
            replacements.append(recreate_source(p))
            return save(*args, **kwargs)

        monkeypatch.setattr(p.store, method, replace_before_commit)
        with pytest.raises((AccessDenied, Conflict)):
            ingest(value, p.store, p.now)

        assert len(replacements) == 1
        assert p.store.receipts("tenant-a") == []
        assert p.matrix()["total"] == 0
        assert stored_source(p) == replacements[0]


def test_legacy_job_remains_usable_only_while_its_legacy_source_exists():
    with local_pipeline() as p:
        p.configure_sources()
        p.store.control.update_item(
            Key={"pk": "T#tenant-a", "sk": "SOURCE#api_supplier"},
            UpdateExpression="REMOVE source_generation",
        )
        p.store.create_job("tenant-a", "legacy-generation-job", {
            "source_id": "api_supplier", "kind": "api", "status": "normalizing",
            "source_generation": None, "scheduled_at": iso(p.now), "row_count": 1,
        })
        value = {
            "schema_version": 1, "tenant_id": "tenant-a", "source_id": "api_supplier",
            "job_id": "legacy-generation-job", "row_number": 1,
            "record": {
                "source_sku": "001-PHONE", "price": "20100000", "currency": "VND",
                "observed_at": iso(p.now - timedelta(minutes=15)),
                "price_basis": "tax_included",
            },
        }
        assert ingest(value, p.store, p.now)["status"] == "accepted"
        replacement = recreate_source(p)
        assert replacement["source_generation"]

        with pytest.raises(AccessDenied):
            p.store.require_job(value)
        assert not p.store.record_source_collection(
            "tenant-a", "api_supplier", "legacy-generation-job", "api", "succeeded", now=p.now,
        )
        assert stored_source(p) == replacement


@pytest.mark.parametrize("replace_during_write", [False, True])
def test_old_pending_approval_cannot_create_mapping_for_recreated_source(replace_during_write, monkeypatch):
    with local_pipeline() as p:
        p.configure_sources()
        p.dispatch()
        p.run_sources("api")
        value = deepcopy(transform_values(p)[0])
        value["record"]["source_sku"] = "UNMAPPED-LIFECYCLE-FIXTURE"
        pending = ingest(value, p.store, p.now)
        assert pending["status"] == "pending"
        replacements = []
        if replace_during_write:
            transact = p.store.client.transact_write_items

            def replace_before_transaction(**kwargs):
                if kwargs["TransactItems"][0].get("ConditionCheck"):
                    assert not replacements
                    replacements.append(recreate_source(p))
                return transact(**kwargs)

            monkeypatch.setattr(p.store.client, "transact_write_items", replace_before_transaction)
        else:
            replacements.append(recreate_source(p))

        with pytest.raises((AccessDenied, Conflict)):
            p.store.approve_pending("tenant-a", pending["observation_id"], "phone", now=p.now)

        assert len(replacements) == 1
        assert p.store.receipt("tenant-a", pending["observation_id"])["status"] == "pending"
        assert p.store.mapping("tenant-a", "api_supplier", value["record"]["source_sku"]) is None
        assert p.matrix()["total"] == 0
        assert stored_source(p) == replacements[0]


def test_queue_payload_cannot_assign_old_job_to_a_new_generation():
    with local_pipeline() as p:
        p.configure_sources()
        p.dispatch()
        p.run_sources("api")
        value = deepcopy(transform_values(p)[0])
        replacement = recreate_source(p)
        value["source_generation"] = replacement["source_generation"]

        with pytest.raises(AccessDenied):
            ingest(value, p.store, p.now)

        assert p.store.receipts("tenant-a") == []
        assert p.matrix()["total"] == 0
        assert stored_source(p) == replacement


def test_csv_upload_captures_generation_and_old_delivery_cannot_read_recreated_source(monkeypatch):
    with local_pipeline() as p:
        monkeypatch.setenv("RAW_BUCKET", p.bucket)
        source = {"source_id": "csv_vendor", "kind": "csv", "enabled": True}
        assert p.api("POST", "/sources", body=source)[0] == 201
        initial = p.store.get("tenant-a", "SOURCE#csv_vendor")
        status, upload = p.api("POST", "/jobs/csv-upload", body={"source_id": "csv_vendor"})
        assert status == 201
        job = p.store.get("tenant-a", "JOB#" + upload["job_id"])
        assert job["source_generation"] == initial["source_generation"]
        raw = p.s3.put_object(Bucket=p.bucket, Key=upload["s3_key"], Body=b"fixture-not-read")
        event = {
            "source": "aws.s3", "detail-type": "Object Created",
            "detail": {
                "bucket": {"name": p.bucket},
                "object": {"key": upload["s3_key"], "version-id": raw["VersionId"]},
            },
        }
        assert p.api("DELETE", "/sources/csv_vendor")[0] == 200
        assert p.api("POST", "/sources", body=source)[0] == 201
        replacement = p.store.get("tenant-a", "SOURCE#csv_vendor")
        assert replacement["source_generation"] != initial["source_generation"]

        def unexpected_read(**kwargs):
            raise AssertionError("a stale CSV job must not read the old upload")

        monkeypatch.setattr(p.s3, "get_object", unexpected_read)
        csv_event(event, p.store, p.s3, p.sqs, p.bucket, p.queues["transform"])

        assert p.store.get("tenant-a", "SOURCE#csv_vendor") == replacement
        assert transform_values(p) == []


@pytest.mark.parametrize("replacement_kind", ["disabled_api", "web"])
def test_obsolete_rows_are_acknowledged_when_replacement_is_disabled_or_another_kind(replacement_kind):
    with local_pipeline() as p:
        p.configure_sources()
        p.dispatch()
        p.run_sources("api")
        values = transform_values(p)
        if replacement_kind == "disabled_api":
            recreate_source(p)
            assert p.api("PATCH", "/sources/api_supplier", body={"enabled": False})[0] == 200
        else:
            assert p.api("DELETE", "/sources/api_supplier")[0] == 200
            assert p.api("POST", "/sources", body={
                "source_id": "api_supplier", "kind": "web", "enabled": True,
                "url": "https://replacement-web.example.invalid/prices",
            })[0] == 201
        replacement = stored_source(p)

        result = normalize_batch({"Records": [
            {"messageId": f"obsolete-{index}", "body": json.dumps(value)}
            for index, value in enumerate(values)
        ]}, p.store, p.now)

        assert result == {"batchItemFailures": []}
        assert p.store.receipts("tenant-a") == []
        assert p.matrix()["total"] == 0
        assert stored_source(p) == replacement


@pytest.mark.parametrize("outcome", ["accepted", "pending"])
def test_disable_reenable_race_retries_current_generation_instead_of_acknowledging_lost_row(outcome, monkeypatch):
    with local_pipeline() as p:
        p.configure_sources()
        p.dispatch()
        p.run_sources("api")
        value = deepcopy(transform_values(p)[0])
        if outcome == "pending":
            value["record"]["source_sku"] = "UNMAPPED-REENABLE-FIXTURE"
        original_generation = stored_source(p)["source_generation"]
        transact = p.store.client.transact_write_items
        cancelled = []

        def toggle_source_around_cancellation(**kwargs):
            assert not cancelled
            assert p.api("PATCH", "/sources/api_supplier", body={"enabled": False})[0] == 200
            try:
                return transact(**kwargs)
            except p.store.client.exceptions.TransactionCanceledException:
                cancelled.append(True)
                assert p.api("PATCH", "/sources/api_supplier", body={"enabled": True})[0] == 200
                raise

        monkeypatch.setattr(p.store.client, "transact_write_items", toggle_source_around_cancellation)
        event = {"Records": [{"messageId": "reenabled-source-row", "body": json.dumps(value)}]}

        result = normalize_batch(event, p.store, p.now)

        assert cancelled == [True]
        assert result == {"batchItemFailures": [{"itemIdentifier": "reenabled-source-row"}]}
        assert p.store.receipts("tenant-a") == []
        assert stored_source(p)["enabled"] is True
        assert stored_source(p)["source_generation"] == original_generation

        monkeypatch.setattr(p.store.client, "transact_write_items", transact)
        assert normalize_batch(event, p.store, p.now) == {"batchItemFailures": []}
        receipts = p.store.receipts("tenant-a")
        assert len(receipts) == 1
        assert receipts[0]["status"] == outcome
