import json
from datetime import timedelta

import pytest

from marketdata.contracts import InvalidRecord, iso
from services.ingestion.web_worker import parse_html_payload
from services.normalizer.app import process_batch as normalize_batch
from tests.conftest import NOW


def html_price(observed_at=None):
    observed_attribute = (
        f' data-observed-at="{observed_at}"' if observed_at is not None else ""
    )
    return (
        '<div data-sku="ABC-01" data-price="120000" data-currency="VND"'
        f'{observed_attribute}></div>'
    ).encode()


def test_source_timestamp_takes_precedence_over_job_schedule():
    rows = parse_html_payload(
        html_price("2026-10-19T07:45:00+07:00"), observed_at=NOW.isoformat()
    )

    assert rows[0]["observed_at"] == "2026-10-19T00:45:00.000000Z"


def test_stale_source_timestamp_remains_rejected_by_normalizer(store):
    source_id, job_id = "web-timestamp", "stale-web-job"
    rows = parse_html_payload(
        html_price(iso(NOW - timedelta(days=31))), observed_at=NOW.isoformat()
    )
    store.put(
        "tenant-a", "SOURCE#" + source_id,
        {"kind": "web", "enabled": True},
    )
    store.create_job(
        "tenant-a", job_id,
        {"kind": "web", "source_id": source_id, "status": "normalizing", "row_count": 1},
    )
    envelope = {
        "schema_version": 1,
        "tenant_id": "tenant-a",
        "source_id": source_id,
        "job_id": job_id,
        "row_number": 1,
        "record": rows[0],
    }

    result = normalize_batch(
        {"Records": [{"messageId": "stale-row", "body": json.dumps(envelope)}]}, store, NOW
    )

    assert result == {"batchItemFailures": []}
    receipts = store.receipts("tenant-a", job_id=job_id)
    assert len(receipts) == 1
    assert receipts[0]["status"] == "rejected"
    assert receipts[0]["reason"] == "outside_retention_window"
    assert store.get("tenant-a", "JOB#" + job_id)["counts"]["rejected"] == 1


@pytest.mark.parametrize("source_timestamp", ["invalid-time", "2026-10-19T00:45:00"])
def test_invalid_source_timestamp_is_not_replaced_by_schedule(source_timestamp):
    with pytest.raises(InvalidRecord, match="invalid_html_observed_at"):
        parse_html_payload(html_price(source_timestamp), observed_at=NOW.isoformat())


def test_missing_source_timestamp_uses_job_schedule():
    rows = parse_html_payload(html_price(), observed_at=NOW.isoformat())

    assert rows[0]["observed_at"] == iso(NOW)
