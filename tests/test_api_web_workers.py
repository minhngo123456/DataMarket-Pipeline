import json
from pathlib import Path

import boto3
import pytest

from marketdata.contracts import InvalidRecord
from services.ingestion.api_worker import (
    parse_api_payload,
)
from services.ingestion.api_worker import (
    process_event as process_api_event,
)
from services.ingestion.web_worker import (
    parse_html_payload,
)
from services.ingestion.web_worker import (
    process_event as process_web_event,
)
from services.normalizer.app import process_batch as process_normalizer_batch
from tests.conftest import NOW


@pytest.fixture
def sqs_transform_queue():
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


def test_api_worker_handles_fetch_error_cleanly(store, sqs_transform_queue):
    sqs, queue = sqs_transform_queue
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
        raise InvalidRecord("api_rate_limited_429")

    event = {
        "schema_version": 1,
        "tenant_id": "tenant-a",
        "source_id": "api_err",
        "job_id": "api-job-err",
        "kind": "api",
        "scheduled_at": "2026-10-19T01:00:00Z",
    }

    process_api_event(event, store, sqs, queue, fetcher=failing_fetcher)
    job = store.get("tenant-a", "JOB#api-job-err")
    assert job["status"] == "failed"
    assert "429" in job["error"]


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
    assert "empty_or_unparseable" in job["error"]


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

    # Exceeding 1 MiB
    with pytest.raises(InvalidRecord, match="exceeds_1_mib"):
        parse_html_payload(b"x" * (1024 * 1024 + 1))
