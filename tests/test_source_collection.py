import json
from copy import deepcopy
from datetime import timedelta

import pytest

from marketdata.contracts import AccessDenied, InvalidRecord, iso, timestamp
from scripts.demo_source_pipeline import local_pipeline, sqs_records
from services.ingestion.api_worker import process_batch as api_batch
from services.ingestion.csv_worker import process_batch as csv_batch
from services.ingestion.csv_worker import process_event as csv_event
from services.ingestion.fetch_errors import RetryableFetchError
from services.ingestion.web_worker import process_batch as web_batch
from tests.conftest import NOW
from tests.test_ingestion import upload


def source_job(store, job_id="health-job", at=NOW, tenant="tenant-a", source_id="health-source"):
    if store.get(tenant, "SOURCE#" + source_id) is None:
        store.put(
            tenant, "SOURCE#" + source_id,
            {"source_id": source_id, "kind": "api", "enabled": True,
             "endpoint": "https://health.example.invalid/prices"},
        )
    store.create_job(
        tenant, job_id,
        {"source_id": source_id, "kind": "api", "status": "normalizing", "row_count": 3,
         "scheduled_at": iso(at), "created_at": iso(at - timedelta(hours=1))},
    )
    return job_id


def record(store, job_id, status, *, now=NOW, error=None, tenant="tenant-a"):
    return store.record_source_collection(
        tenant, "health-source", job_id, "api", status, error_code=error, now=now
    )


def test_collection_success_is_extractor_success_before_normalizer_completion(store):
    job_id = source_job(store)
    before = store.get("tenant-a", "SOURCE#health-source")
    assert "collection_status" not in before
    assert "last_success_at" not in before

    assert record(store, job_id, "running")
    assert record(store, job_id, "succeeded", now=NOW + timedelta(minutes=1))

    source = store.get("tenant-a", "SOURCE#health-source")
    assert source["collection_status"] == "succeeded"
    assert source["last_job_id"] == job_id
    assert source["last_attempt_at"] == iso(NOW)
    assert source["last_success_at"] == iso(NOW + timedelta(minutes=1))
    assert source["last_success_job_id"] == job_id
    assert source["last_success_job_at"] == iso(NOW)
    assert source.get("last_error_code") is None
    job = store.get("tenant-a", "JOB#" + job_id)
    assert job["status"] == "normalizing"
    assert job["collection_started_at"] == iso(NOW)
    assert job["collection_succeeded_at"] == iso(NOW + timedelta(minutes=1))


def test_retry_keeps_previous_success_and_first_attempt_until_enqueue_succeeds(store):
    old = source_job(store, job_id="old-health-job", at=NOW - timedelta(hours=6))
    record(store, old, "running", now=NOW - timedelta(hours=5))
    record(store, old, "succeeded", now=NOW - timedelta(hours=5) + timedelta(minutes=1))
    previous = store.get("tenant-a", "SOURCE#health-source")
    current = source_job(store, job_id="new-health-job")

    record(store, current, "running")
    record(
        store, current, "retrying", now=NOW + timedelta(minutes=1),
        error="api_http_retryable_429",
    )
    retry = store.get("tenant-a", "SOURCE#health-source")
    assert retry["last_job_id"] == current
    assert retry["collection_status"] == "retrying"
    assert retry["last_attempt_at"] == iso(NOW)
    assert retry["last_success_at"] == previous["last_success_at"]
    assert retry["last_success_job_id"] == old
    assert retry["last_error_code"] == "api_http_retryable_429"

    record(store, current, "running", now=NOW + timedelta(minutes=2))
    record(store, current, "succeeded", now=NOW + timedelta(minutes=3))
    succeeded = store.get("tenant-a", "SOURCE#health-source")
    assert succeeded["last_attempt_at"] == iso(NOW)
    assert succeeded["last_success_at"] == iso(NOW + timedelta(minutes=3))
    assert succeeded["last_success_job_id"] == current
    assert succeeded.get("last_error_code") is None


def test_collection_error_never_persists_url_headers_or_credential_text(store):
    job_id = source_job(store)
    record(store, job_id, "running")
    before = store.get("tenant-a", "SOURCE#health-source")
    unsafe = "https://fixture.invalid/prices?token=fixture-value Authorization: Bearer fixture-value"

    with pytest.raises(InvalidRecord, match="invalid_collection_error_code"):
        record(store, job_id, "retrying", error=unsafe, now=NOW + timedelta(seconds=1))

    source = store.get("tenant-a", "SOURCE#health-source")
    assert source == before
    assert "last_error_code" not in source


def test_old_job_outcomes_do_not_clobber_current_status_or_newer_success(store):
    old = source_job(store, job_id="older-job", at=NOW - timedelta(hours=6))
    current = source_job(store, job_id="latest-job")
    record(store, old, "running", now=NOW - timedelta(minutes=10))
    record(store, current, "running")
    record(store, current, "retrying", error="api_http_retryable_429", now=NOW + timedelta(minutes=1))

    record(store, old, "succeeded", now=NOW + timedelta(minutes=2))
    source = store.get("tenant-a", "SOURCE#health-source")
    assert source["last_job_id"] == current
    assert source["collection_status"] == "retrying"
    assert source["last_attempt_at"] == iso(NOW)
    assert source["last_error_code"] == "api_http_retryable_429"
    assert source["last_success_job_id"] == old
    record(store, old, "failed", error="invalid_json_payload", now=NOW + timedelta(minutes=3))
    assert store.get("tenant-a", "SOURCE#health-source") == source

    record(store, current, "succeeded", now=NOW + timedelta(minutes=4))
    latest = store.get("tenant-a", "SOURCE#health-source")
    record(store, old, "succeeded", now=NOW + timedelta(minutes=5))
    assert store.get("tenant-a", "SOURCE#health-source") == latest
    assert latest["last_success_job_id"] == current
    assert latest["last_success_job_at"] == iso(NOW)


def test_success_history_recovers_after_write_failure_and_late_older_success(store, monkeypatch):
    old = source_job(store, job_id="older-success", at=NOW - timedelta(hours=6))
    current = source_job(store, job_id="latest-success")
    record(store, old, "running", now=NOW - timedelta(minutes=1))
    record(store, current, "running")
    update_item = store.control.update_item

    def fail_history(**kwargs):
        if kwargs["UpdateExpression"].startswith("SET last_success_at"):
            raise RuntimeError("simulated_history_write_failure")
        return update_item(**kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store.control, "update_item", fail_history)
        with pytest.raises(RuntimeError, match="simulated_history_write_failure"):
            record(store, current, "succeeded", now=NOW + timedelta(minutes=1))

    record(store, old, "succeeded", now=NOW + timedelta(minutes=2))
    interim = store.get("tenant-a", "SOURCE#health-source")
    assert interim["last_job_id"] == current
    assert interim["collection_status"] == "succeeded"
    assert interim["last_success_job_id"] == old

    record(store, current, "succeeded", now=NOW + timedelta(minutes=3))
    recovered = store.get("tenant-a", "SOURCE#health-source")
    assert recovered["last_success_job_id"] == current
    assert recovered["last_success_at"] == iso(NOW + timedelta(minutes=3))
    assert store.get("tenant-a", "JOB#" + current)["collection_succeeded_at"] == iso(
        NOW + timedelta(minutes=1)
    )
    record(store, current, "succeeded", now=NOW + timedelta(minutes=4))
    assert store.get("tenant-a", "SOURCE#health-source") == recovered


@pytest.mark.parametrize("late_status", ["running", "retrying", "failed"])
def test_successful_job_absorbs_late_running_retry_and_failure(store, late_status):
    job_id = source_job(store)
    record(store, job_id, "running")
    record(store, job_id, "succeeded", now=NOW + timedelta(minutes=1))
    before = store.get("tenant-a", "SOURCE#health-source")

    record(
        store, job_id, late_status, now=NOW + timedelta(minutes=2),
        error="api_worker_error" if late_status != "running" else None,
    )

    assert store.get("tenant-a", "SOURCE#health-source") == before


def test_same_source_and_job_ids_keep_collection_health_in_their_tenant(store):
    job_a = source_job(store, tenant="tenant-a")
    job_b = source_job(store, tenant="tenant-b")
    record(store, job_a, "running", tenant="tenant-a")
    record(store, job_b, "running", tenant="tenant-b")
    record(store, job_a, "succeeded", tenant="tenant-a", now=NOW + timedelta(seconds=1))
    record(store, job_b, "failed", tenant="tenant-b", error="invalid_json_payload")

    source_a = store.get("tenant-a", "SOURCE#health-source")
    source_b = store.get("tenant-b", "SOURCE#health-source")
    assert source_a["collection_status"] == "succeeded"
    assert source_a.get("last_error_code") is None
    assert source_b["collection_status"] == "failed"
    assert source_b["last_error_code"] == "invalid_json_payload"
    assert "last_success_at" not in source_b


@pytest.mark.parametrize("spoof", ["wrong_source", "wrong_kind", "missing_job", "disabled_source", "deleted_source"])
def test_invalid_source_job_or_disabled_deleted_source_cannot_change_health(store, spoof):
    job_id = source_job(store)
    source_id, kind = "health-source", "api"
    if spoof == "wrong_source":
        source_id = "other-source"
        store.put("tenant-a", "SOURCE#other-source", {"kind": "api", "enabled": True})
    elif spoof == "wrong_kind":
        kind = "web"
    elif spoof == "missing_job":
        job_id = "missing-job"
    elif spoof == "disabled_source":
        source = store.get("tenant-a", "SOURCE#health-source")
        store.put("tenant-a", "SOURCE#health-source", {**source, "enabled": False})
    elif spoof == "deleted_source":
        store.control.delete_item(Key={"pk": "T#tenant-a", "sk": "SOURCE#health-source"})
    before = {
        source: store.get("tenant-a", "SOURCE#" + source)
        for source in ("health-source", "other-source")
    }

    changed = store.record_source_collection(
        "tenant-a", source_id, job_id, kind, "running", now=NOW
    )

    assert changed is False
    assert {
        source: store.get("tenant-a", "SOURCE#" + source)
        for source in ("health-source", "other-source")
    } == before


@pytest.mark.parametrize("kind", ["api", "web"])
def test_worker_records_collection_success_before_rows_finish_normalizing(kind):
    with local_pipeline() as p:
        sources = p.configure_sources()
        source_id = sources[kind]["source_id"]
        assert "collection_status" not in p.store.get("tenant-a", "SOURCE#" + source_id)
        p.dispatch()

        run = p.run_sources(kind)

        assert not run["batchItemFailures"]
        job_id = run["bodies"][0]["job_id"]
        source = p.store.get("tenant-a", "SOURCE#" + source_id)
        assert source["collection_status"] == "succeeded"
        assert source["last_job_id"] == source["last_success_job_id"] == job_id
        assert source["last_attempt_at"]
        assert source["last_success_at"]
        assert source["last_success_job_at"] == iso(p.now)
        assert source.get("last_error_code") is None
        assert p.job(job_id)["status"] == "normalizing"
        assert p.normalize() == 3
        assert p.job(job_id)["status"] == "completed"


class PartialQueue:
    def __init__(self, sqs):
        self.sqs = sqs

    def __getattr__(self, name):
        return getattr(self.sqs, name)

    def send_message_batch(self, *, QueueUrl, Entries):
        response = self.sqs.send_message_batch(QueueUrl=QueueUrl, Entries=Entries[:1])
        response["Failed"] = [
            {"Id": entry["Id"], "Code": "InternalError", "SenderFault": False}
            for entry in Entries[1:]
        ]
        return response


def test_partial_send_keeps_previous_success_until_retry_finishes_enqueuing():
    with local_pipeline() as p:
        p.configure_sources()
        p.dispatch()
        p.run_sources("api")
        p.normalize()
        previous = p.store.get("tenant-a", "SOURCE#api_supplier")
        p.advance(hours=6)
        p.dispatch()

        failed = p.run_sources("api", output_sqs=PartialQueue(p.sqs))

        assert len(failed["batchItemFailures"]) == 1
        job_id = failed["bodies"][0]["job_id"]
        retry = p.store.get("tenant-a", "SOURCE#api_supplier")
        assert retry["collection_status"] == "retrying"
        assert retry["last_job_id"] == job_id
        assert retry["last_success_at"] == previous["last_success_at"]
        assert retry["last_success_job_id"] == previous["last_success_job_id"]
        attempt_at = retry["last_attempt_at"]
        p.normalize()
        p.release_retries("api")

        assert not p.run_sources("api")["batchItemFailures"]
        succeeded = p.store.get("tenant-a", "SOURCE#api_supplier")
        assert succeeded["collection_status"] == "succeeded"
        assert succeeded["last_success_job_id"] == job_id
        assert succeeded["last_success_at"] >= previous["last_success_at"]
        assert succeeded["last_attempt_at"] == attempt_at
        assert succeeded.get("last_error_code") is None
        p.normalize()
        assert p.job(job_id)["status"] == "completed"


def test_csv_collection_success_requires_bound_version_and_forged_version_changes_nothing(store):
    s3, sqs, queue, event = upload(
        store, b"source_sku,price,currency,observed_at\n001-PHONE,100,VND,2026-10-19T00:45:00Z\n"
    )

    csv_event(event, store, s3, sqs, "test-raw-bucket", queue)

    job = store.get("tenant-a", "JOB#csv-job")
    source = store.get("tenant-a", "SOURCE#vendor")
    assert job["raw_version"] == event["detail"]["object"]["version-id"]
    assert job["status"] == "normalizing"
    assert source["collection_status"] == "succeeded"
    assert source["last_success_job_id"] == "csv-job"
    forged = deepcopy(event)
    forged["detail"]["object"]["version-id"] = s3.put_object(
        Bucket="test-raw-bucket", Key=event["detail"]["object"]["key"], Body=b"changed"
    )["VersionId"]

    with pytest.raises(AccessDenied, match="version"):
        csv_event(forged, store, s3, sqs, "test-raw-bucket", queue)

    assert store.get("tenant-a", "SOURCE#vendor") == source
    assert store.get("tenant-a", "JOB#csv-job") == job


def test_completed_source_job_duplicate_does_not_create_another_health_attempt():
    with local_pipeline() as p:
        p.configure_sources()
        p.dispatch()
        first = p.run_sources("api")
        p.normalize()
        job_id = first["bodies"][0]["job_id"]
        before = p.store.get("tenant-a", "SOURCE#api_supplier")
        assert p.job(job_id)["status"] == "completed"

        p.dispatch()
        duplicate = p.run_sources("api")

        assert duplicate["received"] == 1
        assert not duplicate["batchItemFailures"]
        assert duplicate["bodies"][0]["job_id"] == job_id
        assert p.store.get("tenant-a", "SOURCE#api_supplier") == before
        assert len(p.fetch_calls) == 1
        assert p.normalize() == 0


@pytest.mark.parametrize("kind,worker", [("api", api_batch), ("web", web_batch)])
def test_late_delivery_cannot_fail_fully_enqueued_job_while_normalizer_is_running(kind, worker):
    class UnavailableQueue:
        def send_message_batch(self, **kwargs):
            raise RuntimeError("simulated_late_send_failure")

    with local_pipeline() as p:
        sources = p.configure_sources()
        p.dispatch()
        first = p.run_sources(kind)
        body = first["bodies"][0]
        before_job = p.store.get("tenant-a", "JOB#" + body["job_id"])
        before_source = p.store.get("tenant-a", "SOURCE#" + sources[kind]["source_id"])
        assert before_job["status"] == "normalizing"
        assert before_job["collection_succeeded_at"]

        result = worker({"Records": [{
            "messageId": "late-delivery", "body": json.dumps(body),
            "attributes": {"ApproximateReceiveCount": "5"},
        }]}, p.store, UnavailableQueue(), p.queues["transform"])

        assert result == {"batchItemFailures": [{"itemIdentifier": "late-delivery"}]}
        assert p.store.get("tenant-a", "JOB#" + body["job_id"]) == before_job
        assert p.store.get("tenant-a", "SOURCE#" + sources[kind]["source_id"]) == before_source
        assert p.normalize() == 3
        assert p.job(body["job_id"])["status"] == "completed"


@pytest.mark.parametrize("kind", ["api", "web"])
def test_terminal_health_write_failure_is_reconciled_on_retry(kind, monkeypatch):
    with local_pipeline() as p:
        sources = p.configure_sources()
        source = sources[kind]
        p.feeds[source.get("endpoint") or source["url"]] = b"invalid source fixture"
        p.dispatch()
        record_collection = p.store.record_source_collection
        failed_writes = []

        def transient_health_write(tenant, source_id, job_id, worker_kind, status, *args, **kwargs):
            if status == "failed" and not failed_writes:
                failed_writes.append(job_id)
                raise RuntimeError("simulated_terminal_health_write_failure")
            return record_collection(tenant, source_id, job_id, worker_kind, status, *args, **kwargs)

        monkeypatch.setattr(p.store, "record_source_collection", transient_health_write)
        first = p.run_sources(kind)
        assert len(first["batchItemFailures"]) == 1
        job_id = first["bodies"][0]["job_id"]
        failed_job = p.store.get("tenant-a", "JOB#" + job_id)
        assert failed_job["status"] == "failed"
        p.release_retries(kind)

        retry = p.run_sources(kind)

        assert retry["received"] == 1 and not retry["batchItemFailures"]
        health = p.store.get("tenant-a", "SOURCE#" + source["source_id"])
        assert health["collection_status"] == "failed"
        assert health["last_error_code"] == failed_job["error"]
        assert p.store.get("tenant-a", "JOB#" + job_id) == failed_job
        assert len(p.fetch_calls) == 1


def previous_worker_success(pipeline, kind):
    sources = pipeline.configure_sources()
    assert pipeline.dispatch() == {"sent": 2}
    first = pipeline.run_sources(kind)
    assert first["received"] == 1 and not first["batchItemFailures"]
    assert pipeline.normalize() == 3
    source_id = sources[kind]["source_id"]
    previous = pipeline.store.get("tenant-a", "SOURCE#" + source_id)
    assert previous["collection_status"] == "succeeded"
    assert pipeline.job(previous["last_job_id"])["status"] == "completed"
    pipeline.advance(hours=6)
    return sources[kind], previous


@pytest.mark.parametrize("kind", ["api", "web"])
def test_connection_retry_sanitizes_url_and_token_in_logs_source_and_job(kind, caplog):
    # Canary text has no real authority and is never sent over the network.
    canary_url = "https://error-canary.example.invalid/prices?token=local-only-token"
    canary_token = "local-only-token"
    unsafe_reason = f"{kind}_connection_error_{canary_url} Authorization: Bearer {canary_token}"
    with local_pipeline() as p:
        source, previous = previous_worker_success(p, kind)
        source_id = source["source_id"]
        url = source.get("endpoint") or source["url"]
        p.feeds[url] = RetryableFetchError(unsafe_reason, retry_after_seconds=1)
        assert p.dispatch() == {"sent": 2}
        caplog.clear()
        caplog.set_level("WARNING")

        for count in range(1, 6):
            if count > 1:
                p.release_retries(kind)
            run = p.run_sources(kind)
            assert run["received"] == 1
            assert run["receive_counts"] == [count]
            assert len(run["batchItemFailures"]) == 1
            job_id = run["bodies"][0]["job_id"]
            job = p.store.get("tenant-a", "JOB#" + job_id)
            health = p.store.get("tenant-a", "SOURCE#" + source_id)
            expected_code = f"{kind}_connection_error"
            if count == 5:
                expected_code = "retry_limit_exceeded_" + expected_code
                assert job["status"] == "failed"
                assert job["error"] == expected_code
            else:
                assert job["status"] == "queued"
                assert "error" not in job
            assert health["collection_status"] == ("failed" if count == 5 else "retrying")
            assert health["last_error_code"] == expected_code
            assert health["last_success_at"] == previous["last_success_at"]
            assert health["last_success_job_id"] == previous["last_success_job_id"]
            stored = json.dumps([health, job], default=str)
            for unsafe in (canary_url, canary_token, "Authorization: Bearer", unsafe_reason):
                assert unsafe not in caplog.text
                assert unsafe not in stored
        assert f"{kind}_connection_error" in caplog.text


@pytest.mark.parametrize("kind", ["api", "web"])
def test_rate_limit_reaches_fifth_sqs_receive_and_preserves_previous_success(kind):
    with local_pipeline() as p:
        source, previous = previous_worker_success(p, kind)
        source_id = source["source_id"]
        url = source.get("endpoint") or source["url"]
        p.feeds[url] = RetryableFetchError(f"{kind}_http_retryable_429", retry_after_seconds=1)
        assert p.dispatch() == {"sent": 2}
        first_attempt = None
        job_id = None

        for count in range(1, 6):
            if count > 1:
                p.release_retries(kind)
            run = p.run_sources(kind)
            assert run["received"] == 1 and run["receive_counts"] == [count]
            assert len(run["batchItemFailures"]) == 1
            current_job = run["bodies"][0]["job_id"]
            job_id = job_id or current_job
            assert current_job == job_id
            job = p.store.get("tenant-a", "JOB#" + job_id)
            health = p.store.get("tenant-a", "SOURCE#" + source_id)
            first_attempt = first_attempt or health["last_attempt_at"]
            assert health["last_attempt_at"] == first_attempt
            assert health["last_job_id"] == job_id != previous["last_job_id"]
            assert health["last_success_at"] == previous["last_success_at"]
            assert health["last_success_job_id"] == previous["last_success_job_id"]
            assert health["last_success_job_at"] == previous["last_success_job_at"]
            assert health["collection_status"] == ("failed" if count == 5 else "retrying")
            code = f"{kind}_http_retryable_429"
            assert health["last_error_code"] == ("retry_limit_exceeded_" + code if count == 5 else code)
            assert job["status"] == ("failed" if count == 5 else "queued")
            assert job["row_count"] == 0
            if count == 5:
                assert job["error"] == "retry_limit_exceeded_" + code
        assert len([call for call in p.fetch_calls if call["url"] == url]) == 6


class TransientCsvClient:
    def __init__(self, client, failing_operation):
        self.client = client
        self.failing_operation = failing_operation
        self.failed_calls = 0

    def __getattr__(self, name):
        if name != self.failing_operation:
            return getattr(self.client, name)

        def fail(**kwargs):
            self.failed_calls += 1
            raise RuntimeError("simulated_csv_infrastructure_failure")

        return fail


@pytest.mark.parametrize("failure_at", ["s3_get", "sqs_send"])
def test_csv_transient_failure_changes_only_bound_version_and_fails_at_fifth_receive(store, failure_at):
    body = b"source_sku,price,currency,observed_at\n001-PHONE,100,VND,2026-10-19T00:45:00Z\n"
    s3, sqs, output_queue, first_event = upload(store, body)
    csv_event(first_event, store, s3, sqs, "test-raw-bucket", output_queue)
    previous = store.get("tenant-a", "SOURCE#vendor")
    previous_job = store.get("tenant-a", "JOB#csv-job")
    tenant_b_source = store.get("tenant-b", "SOURCE#vendor")
    tenant_b_job = store.get("tenant-b", "JOB#csv-job")
    job_id = "csv-retry-health-job"
    key = "tenants/tenant-a/raw/" + job_id
    store.create_job("tenant-a", job_id, {
        "source_id": "vendor", "kind": "csv", "status": "queued", "row_count": 0,
        "raw_key": key,
        "created_at": iso(timestamp(previous_job["collection_job_at"]) + timedelta(hours=6)),
    })
    version = s3.put_object(Bucket="test-raw-bucket", Key=key, Body=body)["VersionId"]
    event = deepcopy(first_event)
    event["detail"]["object"] = {"key": key, "version-id": version}
    input_queue = sqs.create_queue(QueueName="csv-health-input")["QueueUrl"]
    message_id = sqs.send_message(QueueUrl=input_queue, MessageBody=json.dumps(event))["MessageId"]
    failing = TransientCsvClient(
        s3 if failure_at == "s3_get" else sqs,
        "get_object" if failure_at == "s3_get" else "send_message_batch",
    )

    for count in range(1, 6):
        messages = sqs.receive_message(
            QueueUrl=input_queue, MaxNumberOfMessages=1, VisibilityTimeout=0,
            MessageSystemAttributeNames=["ApproximateReceiveCount"],
        ).get("Messages", [])
        assert len(messages) == 1
        assert messages[0]["MessageId"] == message_id
        assert messages[0]["Attributes"]["ApproximateReceiveCount"] == str(count)
        result = csv_batch(
            {"Records": sqs_records(messages)}, store,
            failing if failure_at == "s3_get" else s3,
            failing if failure_at == "sqs_send" else sqs,
            "test-raw-bucket", output_queue,
        )
        assert result == {"batchItemFailures": [{"itemIdentifier": message_id}]}
        job = store.get("tenant-a", "JOB#" + job_id)
        health = store.get("tenant-a", "SOURCE#vendor")
        assert job["raw_version"] == version
        assert job["row_count"] == (0 if failure_at == "s3_get" else 1)
        assert health["last_job_id"] == job_id
        assert health["collection_status"] == ("failed" if count == 5 else "retrying")
        code = "retry_limit_exceeded_csv_worker_error" if count == 5 else "csv_worker_error"
        assert health["last_error_code"] == code
        assert health["last_success_at"] == previous["last_success_at"]
        assert health["last_success_job_id"] == previous["last_success_job_id"]
        if count == 5:
            assert job["status"] == "failed"
            assert job["error"] == code
        else:
            assert job["status"] == ("queued" if failure_at == "s3_get" else "normalizing")

        if count == 1:
            forged = deepcopy(event)
            forged["detail"]["object"]["version-id"] = s3.put_object(
                Bucket="test-raw-bucket", Key=key, Body=b"different upload version"
            )["VersionId"]
            missing = deepcopy(event)
            missing["detail"]["object"].pop("version-id")
            for invalid in (forged, missing):
                invalid_result = csv_batch({"Records": [{
                    "messageId": "invalid-version", "body": json.dumps(invalid),
                    "attributes": {"ApproximateReceiveCount": "5"},
                }]}, store, s3, sqs, "test-raw-bucket", output_queue)
                assert invalid_result == {"batchItemFailures": [{"itemIdentifier": "invalid-version"}]}
                assert store.get("tenant-a", "JOB#" + job_id) == job
                assert store.get("tenant-a", "SOURCE#vendor") == health
        assert store.get("tenant-b", "SOURCE#vendor") == tenant_b_source
        assert store.get("tenant-b", "JOB#csv-job") == tenant_b_job
    assert failing.failed_calls == 5
