import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError

import boto3
import pytest

from marketdata.contracts import InvalidRecord
from services.ingestion.api_worker import (
    parse_api_payload,
)
from services.ingestion.api_worker import process_batch as process_api_batch
from services.ingestion.api_worker import (
    process_event as process_api_event,
)
from services.ingestion.fetch_errors import (
    RetryableFetchError,
    http_error_to_exception,
    parse_retry_after,
    retry_delay_seconds,
)
from services.ingestion.web_worker import (
    parse_html_payload,
)
from services.ingestion.web_worker import process_batch as process_web_batch
from services.ingestion.web_worker import (
    process_event as process_web_event,
)
from services.normalizer.app import process_batch as process_normalizer_batch
from tests.conftest import NOW


@pytest.fixture
def sqs_transform_queue(monkeypatch):
    bucket = "worker-snapshots-test"
    monkeypatch.setenv("SOURCE_SNAPSHOT_BUCKET", bucket)
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket=bucket)
    s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    sqs = boto3.client("sqs")
    queue_url = sqs.create_queue(QueueName="transform-test")["QueueUrl"]
    return sqs, queue_url


def test_api_worker_end_to_end(store, sqs_transform_queue):
    sqs, queue = sqs_transform_queue
    store.put(
        "tenant-a",
        "SOURCE#api_supplier",
        {
            "source_id": "api_supplier",
            "kind": "api",
            "enabled": True,
            "endpoint": "https://api.partner.com/v1/prices",
        },
    )
    store.create_job(
        "tenant-a",
        "api-job-1",
        {
            "source_id": "api_supplier",
            "kind": "api",
            "status": "queued",
            "row_count": 0,
        },
    )

    fixture_content = Path("tests/fixtures/api_response.json").read_bytes()

    def mock_fetcher(url, headers=None):
        return fixture_content

    event = {
        "schema_version": 1,
        "tenant_id": "tenant-a",
        "source_id": "api_supplier",
        "job_id": "api-job-1",
        "kind": "api",
        "scheduled_at": "2026-10-19T01:00:00Z",
    }

    process_api_event(event, store, sqs, queue, fetcher=mock_fetcher)

    job = store.get("tenant-a", "JOB#api-job-1")
    assert job["status"] == "normalizing"
    assert job["row_count"] == 3

    # Pass rows into Normalizer
    messages = sqs.receive_message(QueueUrl=queue, MaxNumberOfMessages=10).get("Messages", [])
    assert len(messages) == 3

    batch = {"Records": [{"messageId": m["MessageId"], "body": m["Body"]} for m in messages]}
    result = process_normalizer_batch(batch, store, NOW)
    assert result == {"batchItemFailures": []}

    # Verify mapping & observation
    counts = store.refresh_job("tenant-a", "api-job-1")
    assert counts["accepted"] + counts["pending"] == 3


def test_web_worker_end_to_end(store, sqs_transform_queue):
    sqs, queue = sqs_transform_queue
    store.put(
        "tenant-a",
        "SOURCE#web_competitor",
        {
            "source_id": "web_competitor",
            "kind": "web",
            "enabled": True,
            "url": "https://competitor.vn/bang-gia",
        },
    )
    store.create_job(
        "tenant-a",
        "web-job-1",
        {
            "source_id": "web_competitor",
            "kind": "web",
            "status": "queued",
            "row_count": 0,
        },
    )

    fixture_content = Path("tests/fixtures/sample_page.html").read_bytes()

    def mock_fetcher(url, headers=None):
        return fixture_content

    event = {
        "schema_version": 1,
        "tenant_id": "tenant-a",
        "source_id": "web_competitor",
        "job_id": "web-job-1",
        "kind": "web",
        "scheduled_at": "2026-10-19T01:00:00Z",
    }

    process_web_event(event, store, sqs, queue, fetcher=mock_fetcher)

    job = store.get("tenant-a", "JOB#web-job-1")
    assert job["status"] == "normalizing"
    assert job["row_count"] == 3

    messages = sqs.receive_message(QueueUrl=queue, MaxNumberOfMessages=10).get("Messages", [])
    assert len(messages) == 3

    batch = {"Records": [{"messageId": m["MessageId"], "body": m["Body"]} for m in messages]}
    result = process_normalizer_batch(batch, store, NOW)
    assert result == {"batchItemFailures": []}


def test_api_worker_retries_rate_limit_with_bounded_backoff(store, sqs_transform_queue):
    _, queue = sqs_transform_queue
    store.put(
        "tenant-a",
        "SOURCE#api_err",
        {"source_id": "api_err", "kind": "api", "enabled": True, "endpoint": "https://api.err.com"},
    )
    store.create_job(
        "tenant-a",
        "api-job-err",
        {"source_id": "api_err", "kind": "api", "status": "queued", "row_count": 0},
    )

    def failing_fetcher(url, headers=None):
        raise RetryableFetchError("api_http_retryable_429", retry_after_seconds=90)

    event = {
        "schema_version": 1,
        "tenant_id": "tenant-a",
        "source_id": "api_err",
        "job_id": "api-job-err",
        "kind": "api",
        "scheduled_at": "2026-10-19T01:00:00Z",
    }

    class SqsSpy:
        def __init__(self):
            self.visibility_calls = []

        def change_message_visibility(self, **kwargs):
            self.visibility_calls.append(kwargs)

    sqs = SqsSpy()
    result = process_api_batch(
        {
            "Records": [
                {
                    "messageId": "api-retry",
                    "receiptHandle": "receipt",
                    "attributes": {"ApproximateReceiveCount": "2"},
                    "body": json.dumps(event),
                }
            ]
        },
        store,
        sqs,
        queue,
        fetcher=failing_fetcher,
        input_queue_url="api-input-queue",
    )

    assert result == {"batchItemFailures": [{"itemIdentifier": "api-retry"}]}
    job = store.get("tenant-a", "JOB#api-job-err")
    assert job["status"] == "queued"
    assert sqs.visibility_calls == [
        {
            "QueueUrl": "api-input-queue",
            "ReceiptHandle": "receipt",
            "VisibilityTimeout": 90,
        }
    ]


def test_retry_exhaustion_marks_job_failed_and_leaves_message_for_dlq(store, sqs_transform_queue):
    _, queue = sqs_transform_queue
    store.put(
        "tenant-a",
        "SOURCE#api_err",
        {"source_id": "api_err", "kind": "api", "enabled": True, "endpoint": "https://api.err.com"},
    )
    store.create_job(
        "tenant-a",
        "api-job-exhausted",
        {"source_id": "api_err", "kind": "api", "status": "queued", "row_count": 0},
    )
    event = {
        "schema_version": 1,
        "tenant_id": "tenant-a",
        "source_id": "api_err",
        "job_id": "api-job-exhausted",
        "kind": "api",
    }

    def failing_fetcher(url, headers=None):
        raise RetryableFetchError("api_http_retryable_503")

    class SqsSpy:
        def change_message_visibility(self, **kwargs):
            raise AssertionError("exhausted retries should not be delayed again")

    result = process_api_batch(
        {
            "Records": [
                {
                    "messageId": "api-last-attempt",
                    "receiptHandle": "receipt",
                    "attributes": {"ApproximateReceiveCount": "5"},
                    "body": json.dumps(event),
                }
            ]
        },
        store,
        SqsSpy(),
        queue,
        fetcher=failing_fetcher,
        input_queue_url="api-input-queue",
    )

    assert result == {"batchItemFailures": [{"itemIdentifier": "api-last-attempt"}]}
    job = store.get("tenant-a", "JOB#api-job-exhausted")
    assert job["status"] == "failed"
    assert job["error"] == "retry_limit_exceeded_api_http_retryable_503"


def test_web_worker_retries_temporary_failure(store, sqs_transform_queue):
    _, queue = sqs_transform_queue
    store.put(
        "tenant-a",
        "SOURCE#web_retry",
        {"source_id": "web_retry", "kind": "web", "enabled": True, "url": "https://web.err.com"},
    )
    store.create_job(
        "tenant-a",
        "web-job-retry",
        {"source_id": "web_retry", "kind": "web", "status": "queued", "row_count": 0},
    )
    event = {
        "schema_version": 1,
        "tenant_id": "tenant-a",
        "source_id": "web_retry",
        "job_id": "web-job-retry",
        "kind": "web",
    }

    def failing_fetcher(url, headers=None):
        raise RetryableFetchError("web_http_retryable_429", retry_after_seconds=45)

    class SqsSpy:
        def __init__(self):
            self.visibility_calls = []

        def change_message_visibility(self, **kwargs):
            self.visibility_calls.append(kwargs)

    sqs = SqsSpy()
    result = process_web_batch(
        {
            "Records": [
                {
                    "messageId": "web-retry",
                    "receiptHandle": "receipt",
                    "attributes": {"ApproximateReceiveCount": "1"},
                    "body": json.dumps(event),
                }
            ]
        },
        store,
        sqs,
        queue,
        fetcher=failing_fetcher,
        input_queue_url="web-input-queue",
    )

    assert result == {"batchItemFailures": [{"itemIdentifier": "web-retry"}]}
    assert store.get("tenant-a", "JOB#web-job-retry")["status"] == "queued"
    assert sqs.visibility_calls[0]["VisibilityTimeout"] == 45


def test_http_error_classification_and_retry_after():
    now = datetime(2026, 10, 21, 0, 0, tzinfo=UTC)
    assert parse_retry_after("90", now=now) == 90
    assert parse_retry_after("Wed, 21 Oct 2026 00:02:00 GMT", now=now) == 120
    assert retry_delay_seconds(RetryableFetchError("429", retry_after_seconds=90), 2) == 90
    assert retry_delay_seconds(RetryableFetchError("503"), 5) == 300

    too_many_requests = HTTPError(
        "https://api.example/prices", 429, "Too Many Requests", {"Retry-After": "75"}, None
    )
    assert http_error_to_exception(too_many_requests, "api").retry_after_seconds == 75
    forbidden = HTTPError("https://api.example/prices", 403, "Forbidden", {}, None)
    assert str(http_error_to_exception(forbidden, "api")) == "api_access_denied_403"


def test_web_worker_handles_invalid_html(store, sqs_transform_queue):
    sqs, queue = sqs_transform_queue
    store.put(
        "tenant-a",
        "SOURCE#web_err",
        {"source_id": "web_err", "kind": "web", "enabled": True, "url": "https://web.err.com"},
    )
    store.create_job(
        "tenant-a",
        "web-job-err",
        {"source_id": "web_err", "kind": "web", "status": "queued", "row_count": 0},
    )

    def mock_fetcher(url, headers=None):
        return b"<html><body>No table here</body></html>"

    event = {
        "schema_version": 1,
        "tenant_id": "tenant-a",
        "source_id": "web_err",
        "job_id": "web-job-err",
        "kind": "web",
        "scheduled_at": "2026-10-19T01:00:00Z",
    }

    process_web_event(event, store, sqs, queue, fetcher=mock_fetcher)
    job = store.get("tenant-a", "JOB#web-job-err")
    assert job["status"] == "failed"
    assert job["error"] == "html_products_not_found"
    assert "source_snapshot" not in job


@pytest.mark.parametrize(
    ("kind", "process_worker"),
    [("api", process_api_event), ("web", process_web_event)],
)
def test_disabled_source_fails_job_without_retry(store, sqs_transform_queue, kind, process_worker):
    sqs, queue = sqs_transform_queue
    source_id = f"{kind}_disabled"
    job_id = f"{kind}-job-disabled"
    source = {"source_id": source_id, "kind": kind, "enabled": False}
    source["endpoint" if kind == "api" else "url"] = "https://source.example/prices"
    store.put("tenant-a", f"SOURCE#{source_id}", source)
    store.create_job(
        "tenant-a",
        job_id,
        {"source_id": source_id, "kind": kind, "status": "queued", "row_count": 0},
    )
    event = {
        "schema_version": 1,
        "tenant_id": "tenant-a",
        "source_id": source_id,
        "job_id": job_id,
        "kind": kind,
    }

    process_worker(event, store, sqs, queue)

    job = store.get("tenant-a", f"JOB#{job_id}")
    assert job["status"] == "failed"
    assert job["error"] == "source_disabled_or_missing"


def test_payload_parsers_validation():
    # Empty JSON
    with pytest.raises(InvalidRecord, match="empty_api_response"):
        parse_api_payload(b"[]")

    # Injected tenant_id
    with pytest.raises(InvalidRecord, match="tenant_not_allowed"):
        parse_api_payload(
            json.dumps(
                [
                    {
                        "source_sku": "SKU1",
                        "price": "100",
                        "currency": "VND",
                        "observed_at": "2026-10-19T01:00:00Z",
                        "tenant_id": "evil-tenant",
                    }
                ]
            ).encode()
        )

    # HTML supports larger storefront pages, while CSV/API retain their own limits.
    with pytest.raises(InvalidRecord, match="html_response_exceeds_10_mib"):
        parse_html_payload(b"x" * (10 * 1024 * 1024 + 1))


def test_html_parser_normalizes_common_header_aliases_and_rejects_empty_fields():
    html = b"""
    <table>
      <tr><th>Product SKU</th><th>Unit Price</th><th>Currency</th><th>Date</th></tr>
      <tr><td>ABC-01</td><td>120000</td><td>VND</td><td>2026-10-19T00:45:00Z</td></tr>
    </table>
    """
    assert parse_html_payload(html) == [
        {
            "source_sku": "ABC-01",
            "price": "120000",
            "currency": "VND",
            "observed_at": "2026-10-19T00:45:00.000000Z",
        }
    ]

    assert parse_html_payload(
        b'<div data-sku="ABC-01" data-price="120000"></div>',
        observed_at=NOW.isoformat(),
    ) == [
        {
            "source_sku": "ABC-01",
            "price": "120000",
            "currency": "VND",
            "observed_at": "2026-10-19T01:00:00.000000Z",
        }
    ]
