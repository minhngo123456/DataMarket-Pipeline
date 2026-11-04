import json
from collections import Counter
from datetime import datetime, timedelta

import boto3
import pytest

from marketdata.contracts import InvalidRecord, iso
from services.ingestion.api_worker import process_batch as api_batch
from services.ingestion.batch import process_source_batch
from services.ingestion.fetch_errors import RetryableFetchError
from services.ingestion.snapshot import SourceSnapshots
from services.ingestion.web_worker import process_batch as web_batch
from services.normalizer.app import process_batch as normalize_batch
from tests.conftest import NOW

WORKERS = {"api": api_batch, "web": web_batch}


@pytest.fixture
def snapshot_resources(store, monkeypatch):
    s3 = boto3.client("s3")
    bucket = "source-snapshot-tests"
    s3.create_bucket(Bucket=bucket)
    s3.put_bucket_versioning(
        Bucket=bucket, VersioningConfiguration={"Status": "Enabled"}
    )
    monkeypatch.setenv("SOURCE_SNAPSHOT_BUCKET", bucket)
    sqs = boto3.client("sqs")
    queue = sqs.create_queue(QueueName="snapshot-transform")["QueueUrl"]
    return s3, bucket, sqs, queue


def source_job(store, kind, suffix=""):
    source_id = f"snapshot-{kind}{suffix}"
    job_id = f"snapshot-{kind}{suffix}-job"
    store.put(
        "tenant-a", "SOURCE#" + source_id,
        {"source_id": source_id, "kind": kind, "enabled": True,
         "endpoint" if kind == "api" else "url": "https://synthetic.invalid/prices"},
    )
    store.create_job(
        "tenant-a", job_id,
        {"source_id": source_id, "kind": kind, "status": "queued", "row_count": 0},
    )
    event = {
        "schema_version": 1,
        "tenant_id": "tenant-a",
        "source_id": source_id,
        "job_id": job_id,
        "kind": kind,
        "scheduled_at": NOW.isoformat(),
    }
    return event


def price_rows():
    return [
        {"source_sku": sku, "price": price, "currency": "VND",
         "observed_at": "2026-10-19T00:45:00.000000Z", "price_basis": "tax_included"}
        for sku, price in (("SNAPSHOT-A", "100"), ("SNAPSHOT-B", "200"))
    ]


def source_payload(kind, rows):
    if kind == "api":
        return json.dumps({"items": rows}).encode()
    return "".join(
        f'<div data-sku="{row["source_sku"]}" data-price="{row["price"]}" '
        f'data-currency="{row["currency"]}" data-observed-at="{row["observed_at"]}" '
        f'data-price-basis="{row["price_basis"]}"></div>'
        for row in rows
    ).encode()


def source_message(event, receive_count=1):
    return {
        "Records": [{"messageId": "source-message", "body": json.dumps(event),
                     "attributes": {"ApproximateReceiveCount": str(receive_count)}}]
    }


def drain_normalizer(sqs, queue, store):
    count = 0
    while True:
        messages = sqs.receive_message(QueueUrl=queue, MaxNumberOfMessages=10).get(
            "Messages", []
        )
        if not messages:
            return count
        event = {"Records": [
            {"messageId": message["MessageId"], "body": message["Body"]}
            for message in messages
        ]}
        assert normalize_batch(event, store, NOW) == {"batchItemFailures": []}
        for message in messages:
            sqs.delete_message(QueueUrl=queue, ReceiptHandle=message["ReceiptHandle"])
        count += len(messages)


class PartialQueue:
    def __init__(self, sqs):
        self.sqs = sqs

    def send_message_batch(self, *, QueueUrl, Entries):
        assert len(Entries) == 2
        response = self.sqs.send_message_batch(QueueUrl=QueueUrl, Entries=Entries[:1])
        response["Failed"] = [
            {"Id": Entries[1]["Id"], "Code": "InternalError", "SenderFault": False}
        ]
        return response


@pytest.mark.parametrize("kind", ["api", "web"])
@pytest.mark.parametrize("changed_source", ["reordered", "shorter"])
def test_retry_reuses_snapshot_after_partial_send(
    store, snapshot_resources, kind, changed_source
):
    _, _, sqs, queue = snapshot_resources
    event = source_job(store, kind)
    rows = price_rows()
    for row, product_id in zip(rows, ("snapshot-a", "snapshot-b"), strict=True):
        store.create_product("tenant-a", product_id, product_id)
        store.set_mapping("tenant-a", event["source_id"], row["source_sku"], product_id)
    fetches = []

    def fetcher(url, headers=None):
        fetches.append(url)
        if len(fetches) == 1:
            return source_payload(kind, rows)
        changed = list(reversed(rows)) if changed_source == "reordered" else rows[:1]
        return source_payload(kind, changed)

    first = WORKERS[kind](
        source_message(event), store, PartialQueue(sqs), queue, fetcher=fetcher
    )
    assert first == {"batchItemFailures": [{"itemIdentifier": "source-message"}]}
    assert drain_normalizer(sqs, queue, store) == 1

    second = WORKERS[kind](
        source_message(event, receive_count=2), store, sqs, queue, fetcher=fetcher
    )
    assert second == {"batchItemFailures": []}
    drain_normalizer(sqs, queue, store)

    assert len(fetches) == 1
    receipts = store.receipts("tenant-a", job_id=event["job_id"])
    assert Counter(row["source_sku"] for row in receipts) == {
        "SNAPSHOT-A": 1, "SNAPSHOT-B": 1
    }
    job = store.get("tenant-a", "JOB#" + event["job_id"])
    assert job["status"] == "completed"
    assert job["row_count"] == 2
    assert job["counts"] == {"accepted": 2, "pending": 0, "rejected": 0}


@pytest.mark.parametrize("kind", ["api", "web"])
def test_completed_duplicate_does_not_fetch_send_or_reset_job(
    store, snapshot_resources, kind
):
    _, _, sqs, queue = snapshot_resources
    event = source_job(store, kind)
    first = WORKERS[kind](
        source_message(event), store, sqs, queue,
        fetcher=lambda url, headers=None: source_payload(kind, price_rows()),
    )
    assert first == {"batchItemFailures": []}
    assert drain_normalizer(sqs, queue, store) == 2
    before = store.get("tenant-a", "JOB#" + event["job_id"])
    assert before["status"] == "completed"

    def unexpected_fetch(url, headers=None):
        raise AssertionError("completed job must not fetch again")

    class UnexpectedQueue:
        def send_message_batch(self, **kwargs):
            raise AssertionError("completed job must not send rows again")

    result = WORKERS[kind](
        source_message(event, receive_count=2), store, UnexpectedQueue(), queue,
        fetcher=unexpected_fetch,
    )

    assert result == {"batchItemFailures": []}
    assert store.get("tenant-a", "JOB#" + event["job_id"]) == before
    assert not sqs.receive_message(QueueUrl=queue).get("Messages")


@pytest.mark.parametrize("kind", ["api", "web"])
def test_missing_bound_snapshot_does_not_refetch_or_reset_row_count(
    store, snapshot_resources, kind
):
    s3, bucket, sqs, queue = snapshot_resources
    event = source_job(store, kind)
    fetches = []

    def fetcher(url, headers=None):
        fetches.append(url)
        return source_payload(kind, price_rows())

    first = WORKERS[kind](
        source_message(event), store, PartialQueue(sqs), queue, fetcher=fetcher
    )
    assert first == {"batchItemFailures": [{"itemIdentifier": "source-message"}]}
    job = store.get("tenant-a", "JOB#" + event["job_id"])
    descriptor = job["source_snapshot"]
    s3.delete_object(
        Bucket=bucket, Key=descriptor["key"], VersionId=descriptor["version_id"]
    )

    result = WORKERS[kind](
        source_message(event, receive_count=2), store, sqs, queue, fetcher=fetcher
    )

    assert result == {"batchItemFailures": [{"itemIdentifier": "source-message"}]}
    assert len(fetches) == 1
    after = store.get("tenant-a", "JOB#" + event["job_id"])
    assert after["row_count"] == 2
    assert after["source_snapshot"] == descriptor


@pytest.mark.parametrize("kind", ["api", "web"])
def test_snapshot_larger_than_dynamodb_item_limit_uses_s3(
    store, snapshot_resources, kind
):
    s3, bucket, sqs, queue = snapshot_resources
    event = source_job(store, kind, suffix="-large-payload-source")
    rows = [
        {**price_rows()[0], "source_sku": f"SKU-{number:04d}-" + "X" * 140,
         "price": "123456789012345"}
        for number in range(1000)
    ]
    payload = source_payload(kind, rows)
    assert len(payload) < 1024 * 1024

    result = WORKERS[kind](
        source_message(event), store, sqs, queue,
        fetcher=lambda url, headers=None: payload,
    )

    assert result == {"batchItemFailures": []}
    job = store.get("tenant-a", "JOB#" + event["job_id"])
    assert job["row_count"] == 1000
    descriptor = job["source_snapshot"]
    assert set(descriptor) >= {"key", "version_id", "sha256"}
    snapshot = s3.get_object(
        Bucket=bucket, Key=descriptor["key"], VersionId=descriptor["version_id"]
    )
    assert snapshot["ContentLength"] > 400 * 1024
    assert len(json.dumps(job, default=str).encode()) < 400 * 1024


@pytest.mark.parametrize("kind", ["api", "web"])
def test_retry_after_snapshot_put_before_job_bind_does_not_refetch(
    store, snapshot_resources, kind
):
    s3, bucket, sqs, queue = snapshot_resources
    event = source_job(store, kind)
    job = store.get("tenant-a", "JOB#" + event["job_id"])
    persisted = SourceSnapshots(s3, bucket).get_or_create(event, job, price_rows)
    assert "source_snapshot" not in store.get("tenant-a", "JOB#" + event["job_id"])
    fetches = []

    def unexpected_fetch(url, headers=None):
        fetches.append(url)
        raise AssertionError("S3 snapshot must survive a crash before job binding")

    result = WORKERS[kind](
        source_message(event, receive_count=2), store, sqs, queue,
        fetcher=unexpected_fetch,
    )

    assert result == {"batchItemFailures": []}
    assert fetches == []
    assert drain_normalizer(sqs, queue, store) == 2
    after = store.get("tenant-a", "JOB#" + event["job_id"])
    assert after["source_snapshot"] == persisted.descriptor
    assert after["row_count"] == 2
    assert after["status"] == "completed"


@pytest.mark.parametrize("kind", ["api", "web"])
def test_competing_snapshot_candidate_reuses_the_same_conditional_write_winner(
    store, snapshot_resources, kind
):
    s3, bucket, _, _ = snapshot_resources
    event = source_job(store, kind)
    job = store.get("tenant-a", "JOB#" + event["job_id"])
    winner = []
    winning_rows = list(reversed(price_rows()))

    def fetch_losing_candidate():
        # The first delivery has already observed no object. Interleave a second
        # delivery here so it writes its candidate before the first can write.
        winner.append(SourceSnapshots(s3, bucket).get_or_create(
            event, job, lambda: winning_rows
        ))
        return price_rows()

    recovered = SourceSnapshots(s3, bucket).get_or_create(
        event, job, fetch_losing_candidate
    )

    assert recovered.values == winner[0].values
    assert recovered.descriptor == winner[0].descriptor
    assert [value["record"]["source_sku"] for value in recovered.values] == [
        "SNAPSHOT-B", "SNAPSHOT-A"
    ]
    versions = s3.list_object_versions(
        Bucket=bucket, Prefix=recovered.descriptor["key"]
    )["Versions"]
    assert len(versions) == 1


@pytest.mark.parametrize("kind", ["api", "web"])
@pytest.mark.parametrize("corruption", ["other_tenant", "wrong_hash"])
def test_invalid_snapshot_pointer_retries_without_refetch_or_row_count_reset(
    store, snapshot_resources, kind, corruption
):
    _, _, sqs, queue = snapshot_resources
    event = source_job(store, kind)
    fetches = []

    def fetcher(url, headers=None):
        fetches.append(url)
        return source_payload(kind, price_rows())

    assert WORKERS[kind](
        source_message(event), store, PartialQueue(sqs), queue, fetcher=fetcher
    ) == {"batchItemFailures": [{"itemIdentifier": "source-message"}]}
    job = store.get("tenant-a", "JOB#" + event["job_id"])
    descriptor = dict(job["source_snapshot"])
    if corruption == "other_tenant":
        descriptor["key"] = descriptor["key"].replace("/tenant-a/", "/tenant-b/")
    else:
        descriptor["sha256"] = "0" * 64
    store.update_job("tenant-a", event["job_id"], source_snapshot=descriptor)

    result = WORKERS[kind](
        source_message(event, receive_count=2), store, sqs, queue, fetcher=fetcher
    )

    assert result == {"batchItemFailures": [{"itemIdentifier": "source-message"}]}
    assert len(fetches) == 1
    after = store.get("tenant-a", "JOB#" + event["job_id"])
    assert after["status"] == "normalizing"
    assert after["row_count"] == 2
    assert after["source_snapshot"] == descriptor


def test_manual_web_retry_keeps_first_capture_time_when_source_has_no_timestamp(
    store, snapshot_resources, monkeypatch
):
    _, _, sqs, queue = snapshot_resources
    event = source_job(store, "web")
    event.pop("scheduled_at")
    first_capture = NOW - timedelta(minutes=15)
    clock = [first_capture]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz is not None else clock[0].replace(tzinfo=None)

    monkeypatch.setattr("services.ingestion.web_worker.datetime", Clock)
    payload = source_payload("web", price_rows()).replace(
        b' data-observed-at="2026-10-19T00:45:00.000000Z"', b""
    )
    fetches = []

    def fetcher(url, headers=None):
        fetches.append(url)
        return payload

    assert web_batch(
        source_message(event), store, PartialQueue(sqs), queue, fetcher=fetcher
    ) == {"batchItemFailures": [{"itemIdentifier": "source-message"}]}
    assert drain_normalizer(sqs, queue, store) == 1
    clock[0] = NOW - timedelta(minutes=2)

    assert web_batch(
        source_message(event, receive_count=2), store, sqs, queue, fetcher=fetcher
    ) == {"batchItemFailures": []}
    drain_normalizer(sqs, queue, store)

    assert len(fetches) == 1
    receipts = store.receipts("tenant-a", job_id=event["job_id"])
    assert len(receipts) == 2
    assert {row["observed_at"] for row in receipts} == {iso(first_capture)}
    job = store.get("tenant-a", "JOB#" + event["job_id"])
    assert job["status"] == "completed"
    assert job["row_count"] == 2


@pytest.mark.parametrize("kind", ["api", "web"])
@pytest.mark.parametrize("late_failure", ["retry_exhaustion", "invalid_message"])
def test_late_batch_failure_cannot_undo_normalizer_completion(
    store, snapshot_resources, kind, late_failure
):
    _, _, sqs, queue = snapshot_resources
    event = source_job(store, kind)
    counts = {"accepted": 2, "pending": 0, "rejected": 0}
    store.update_job("tenant-a", event["job_id"], row_count=2, status="normalizing")

    def late_delivery(body, store, sqs, queue, fetcher=None):
        # A delivery was already in progress while the Normalizer finished the
        # same job; its subsequent error must not overwrite durable completion.
        store.update_job("tenant-a", event["job_id"], status="completed", counts=counts)
        if late_failure == "retry_exhaustion":
            raise RetryableFetchError(f"{kind}_http_retryable_503")
        raise InvalidRecord("unsupported_schema_version")

    result = process_source_batch(
        source_message(event, receive_count=5), store, sqs, queue, None, kind, late_delivery
    )

    expected_failures = (
        [{"itemIdentifier": "source-message"}] if late_failure == "retry_exhaustion" else []
    )
    assert result == {"batchItemFailures": expected_failures}
    job = store.get("tenant-a", "JOB#" + event["job_id"])
    assert job["status"] == "completed"
    assert job["row_count"] == 2
    assert job["counts"] == counts
    assert "error" not in job


@pytest.mark.parametrize("kind", ["api", "web"])
def test_legacy_normalizing_job_without_snapshot_retries_without_refetch(
    store, snapshot_resources, kind
):
    s3, bucket, sqs, queue = snapshot_resources
    event = source_job(store, kind)
    store.update_job("tenant-a", event["job_id"], row_count=2, status="normalizing")
    fetches = []

    def fetcher(url, headers=None):
        fetches.append(url)
        return source_payload(kind, price_rows())

    result = WORKERS[kind](
        source_message(event, receive_count=2), store, sqs, queue, fetcher=fetcher
    )

    assert result == {"batchItemFailures": [{"itemIdentifier": "source-message"}]}
    assert fetches == []
    job = store.get("tenant-a", "JOB#" + event["job_id"])
    assert job["status"] == "normalizing"
    assert job["row_count"] == 2
    assert "source_snapshot" not in job
    assert not s3.list_objects_v2(Bucket=bucket).get("Contents")
    assert not sqs.receive_message(QueueUrl=queue).get("Messages")


@pytest.mark.parametrize("kind", ["api", "web"])
@pytest.mark.parametrize("failure", ["invalid_payload", "upstream_timeout"])
def test_invalid_losing_fetch_cannot_fail_snapshot_bound_by_competing_delivery(
    store, snapshot_resources, kind, failure
):
    _, _, sqs, queue = snapshot_resources
    event = source_job(store, kind)
    inner_results = []
    inner_jobs = []

    def losing_fetcher(url, headers=None):
        # The outer delivery has read the queued job and found no snapshot.
        # Its peer now binds valid rows while this older fetch is in progress.
        inner_results.append(WORKERS[kind](
            source_message(event, receive_count=2), store, sqs, queue,
            fetcher=lambda url, headers=None: source_payload(kind, price_rows()),
        ))
        inner_jobs.append(store.get("tenant-a", "JOB#" + event["job_id"]))
        if failure == "upstream_timeout":
            raise RetryableFetchError(f"{kind}_connection_error_timeout")
        return b"{invalid-json" if kind == "api" else b"<html>No price data</html>"

    result = WORKERS[kind](
        source_message(event, receive_count=5), store, sqs, queue, fetcher=losing_fetcher
    )

    failures = [{"itemIdentifier": "source-message"}] if failure == "upstream_timeout" else []
    assert result == {"batchItemFailures": failures}
    assert inner_results == [{"batchItemFailures": []}]
    assert len(inner_jobs) == 1
    inner_job = inner_jobs[0]
    assert inner_job["status"] == "normalizing"
    assert inner_job["row_count"] == 2
    after_outer = store.get("tenant-a", "JOB#" + event["job_id"])
    assert after_outer["status"] == "normalizing"
    assert after_outer["row_count"] == 2
    assert after_outer["source_snapshot"] == inner_job["source_snapshot"]
    assert "error" not in after_outer

    assert drain_normalizer(sqs, queue, store) == 2
    completed = store.get("tenant-a", "JOB#" + event["job_id"])
    assert completed["status"] == "completed"
    assert completed["row_count"] == 2
    assert completed["source_snapshot"] == inner_job["source_snapshot"]
