from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

import pytest

from marketdata.contracts import AccessDenied, Conflict, InvalidRecord, QuotaExceeded
from marketdata.normalize import normalize, sku
from marketdata.queries import history, price_matrix
from marketdata.service import ingest
from services.normalizer.app import process_batch
from tests.conftest import NOW


def test_money_timezone_and_retry(store, message):
    result = ingest(message, store, NOW)
    assert result["status"] == "accepted"
    assert ingest(message, store, NOW)["duplicate"]
    rows = history(store, "tenant-a", "phone", NOW)
    assert len(rows) == 1
    assert rows[0]["schema_version"] == 1
    assert rows[0]["price"] == Decimal("20100000")
    assert rows[0]["observed_at"] == "2026-10-19T00:45:00.000000Z"
    assert sku(" ００１-phone ") == "001-PHONE"


@pytest.mark.parametrize(
    "price", ["20.100.000", "20,100,000", "NaN", "Infinity", "-1", "0", True, "1e7", "1.001", None]
)
def test_invalid_price_is_row_error_not_batch_failure(store, message, price):
    message["record"]["price"] = price
    assert ingest(message, store, NOW)["status"] == "rejected"
    assert not history(store, "tenant-a", "phone", NOW)


@pytest.mark.parametrize(
    "value", ["2026-10-19T00:45:00", "wrong", "2026-10-19T02:00:00Z", "2026-08-01T00:00:00Z"]
)
def test_bad_or_expired_time(store, message, value):
    message["record"]["observed_at"] = value
    assert ingest(message, store, NOW)["status"] == "rejected"


def test_currency_requires_trusted_dated_rate(message):
    raw = {**message["record"], "currency": "USD", "price": "10.25"}
    with pytest.raises(InvalidRecord, match="fx"):
        normalize(raw, {}, NOW)
    row = normalize(
        raw, {"fx": {"currency": "USD", "rate": "25000", "effective_date": "2026-10-18"}}, NOW
    )
    assert row["original_price"] == Decimal("10.25")
    assert row["price"] == 256250
    with pytest.raises(InvalidRecord):
        normalize(
            raw, {"fx": {"currency": "USD", "rate": "25000", "effective_date": "2026-10-20"}}, NOW
        )


def test_unknown_sku_waits_for_explicit_confirmation(store, message):
    message["record"]["source_sku"] = "UNKNOWN"
    result = ingest(message, store, NOW)
    assert result["status"] == "pending"
    assert not price_matrix(store, "tenant-a", NOW)["rows"]
    with pytest.raises(AccessDenied):
        store.approve_pending("tenant-b", result["observation_id"], "phone", now=NOW)
    store.approve_pending("tenant-a", result["observation_id"], "phone", now=NOW)
    store.approve_pending("tenant-a", result["observation_id"], "phone", now=NOW)
    assert len(history(store, "tenant-a", "phone", NOW)) == 1
    assert ingest(message, store, NOW)["duplicate"]
    assert store.mapping("tenant-b", "vendor", "UNKNOWN") is None


def second_job(store, message, job_id="csv-job-2"):
    store.create_job(
        "tenant-a",
        job_id,
        {"source_id": "vendor", "kind": "csv", "status": "queued", "row_count": 1, "raw_key": f"tenants/tenant-a/raw/{job_id}"},
    )
    return {**deepcopy(message), "job_id": job_id}


def test_uploading_same_file_again_adds_no_observation(store, message):
    first = ingest(message, store, NOW)
    repeat = second_job(store, message)
    result = ingest(repeat, store, NOW)

    assert result["status"] == "accepted"
    assert not result["duplicate"]
    assert result["observation_id"] != first["observation_id"]
    assert len(history(store, "tenant-a", "phone", NOW)) == 1
    # The second job still finishes: its own row is counted even though the price point exists.
    assert store.refresh_job("tenant-a", "csv-job-2") == {"accepted": 1, "pending": 0, "rejected": 0}
    assert store.get("tenant-a", "JOB#csv-job-2")["status"] == "completed"
    assert ingest(repeat, store, NOW)["duplicate"]
    assert len(history(store, "tenant-a", "phone", NOW)) == 1


def test_different_price_for_same_sku_and_time_is_kept(store, message):
    ingest(message, store, NOW)
    corrected = second_job(store, message)
    corrected["record"]["price"] = "19900000"
    ingest(corrected, store, NOW)

    rows = history(store, "tenant-a", "phone", NOW)
    assert sorted(row["price"] for row in rows) == [Decimal("19900000"), Decimal("20100000")]


def test_different_time_or_basis_stays_a_separate_observation(store, message):
    ingest(message, store, NOW)
    later = second_job(store, message)
    later["record"]["observed_at"] = "2026-10-19T07:50:00+07:00"
    ingest(later, store, NOW)
    other_basis = second_job(store, message, "csv-job-3")
    other_basis["record"]["price_basis"] = "tax_excluded"
    ingest(other_basis, store, NOW)

    assert len(history(store, "tenant-a", "phone", NOW)) == 3


def test_same_price_point_is_not_shared_between_tenants(store, message):
    ingest(message, store, NOW)
    ingest({**deepcopy(message), "tenant_id": "tenant-b"}, store, NOW)

    assert len(history(store, "tenant-a", "phone", NOW)) == 1
    assert len(history(store, "tenant-b", "phone", NOW)) == 1


def test_approving_two_pending_copies_keeps_one_observation(store, message):
    message["record"]["source_sku"] = "UNKNOWN"
    first = ingest(message, store, NOW)
    second = ingest(second_job(store, message), store, NOW)
    assert first["status"] == second["status"] == "pending"

    store.approve_pending("tenant-a", first["observation_id"], "phone", now=NOW)
    store.approve_pending("tenant-a", second["observation_id"], "phone", now=NOW)

    assert len(history(store, "tenant-a", "phone", NOW)) == 1


def test_pending_receipt_saved_before_content_id_can_still_be_approved(store, message):
    message["record"]["source_sku"] = "UNKNOWN"
    result = ingest(message, store, NOW)
    receipt = store.receipt("tenant-a", result["observation_id"])
    receipt.pop("content_id")
    store.control.put_item(Item=receipt)

    store.approve_pending("tenant-a", result["observation_id"], "phone", now=NOW)

    rows = history(store, "tenant-a", "phone", NOW)
    assert [row["observation_id"] for row in rows] == [result["observation_id"]]


def test_catalog_limit_is_atomic_and_duplicate_does_not_increment(store):
    tenant = store.tenant("tenant-a")
    store.put("tenant-a", "META", {**tenant, "max_skus": 2})
    store.create_product("tenant-a", "laptop", "Laptop")
    with pytest.raises(Conflict):
        store.create_product("tenant-a", "laptop", "Duplicate")
    with pytest.raises(QuotaExceeded):
        store.create_product("tenant-a", "third", "Third")
    assert store.tenant("tenant-a")["product_count"] == 2
    assert store.tenant("tenant-b")["product_count"] == 1


def test_cannot_use_other_tenants_job_or_source(store, message):
    store.create_job(
        "tenant-b",
        "only-b",
        {"source_id": "vendor", "kind": "csv", "status": "queued", "row_count": 100},
    )
    message["job_id"] = "only-b"
    with pytest.raises(AccessDenied):
        ingest(message, store, NOW)
    message["job_id"] = "csv-job"
    message["record"]["tenant_id"] = "tenant-b"
    assert ingest(message, store, NOW)["status"] == "rejected"
    assert not history(store, "tenant-b", "phone", NOW)


def test_disabled_source_and_cancelled_job(store, message):
    store.put("tenant-a", "SOURCE#vendor", {"kind": "csv", "enabled": False})
    with pytest.raises(AccessDenied):
        ingest(message, store, NOW)
    store.put("tenant-a", "SOURCE#vendor", {"kind": "csv", "enabled": True})
    store.update_job("tenant-a", "csv-job", status="cancelled")
    with pytest.raises(AccessDenied):
        ingest(message, store, NOW)


def test_matrix_uses_latest_price_not_minimum_and_excludes_stale(store, message):
    store.put("tenant-a", "SOURCE#internal", {"kind": "csv", "enabled": True})
    store.set_mapping("tenant-a", "internal", "001-PHONE", "phone")
    store.create_job(
        "tenant-a",
        "internal-job",
        {"kind": "csv", "source_id": "internal", "status": "queued", "row_count": 1},
    )
    base = deepcopy(message)
    base.update(source_id="internal", job_id="internal-job")
    base["record"]["price"] = "20500000"
    ingest(base, store, NOW)
    older = deepcopy(message)
    older["record"].update(price="10000000", observed_at="2026-10-18T10:00:00Z")
    older["row_number"] = 2
    ingest(older, store, NOW)
    ingest(message, store, NOW)
    stale = deepcopy(message)
    stale["row_number"] = 3
    stale["record"].update(price="1", observed_at="2026-10-17T01:00:00Z")
    ingest(stale, store, NOW)
    rows = price_matrix(store, "tenant-a", NOW)["rows"]
    assert len(rows) == 2
    competitor = next(r for r in rows if r["source_id"] == "vendor")
    assert competitor["price"] == 20100000
    assert competitor["change_percent"] == Decimal("-1.95")
    assert len(history(store, "tenant-a", "phone", NOW)) == 4
    assert not price_matrix(store, "tenant-b", NOW)["rows"]
    assert not history(store, "tenant-a", "phone", NOW + timedelta(days=31))


def test_unknown_price_basis_is_not_comparable(store, message):
    message["record"].pop("price_basis")
    ingest(message, store, NOW)
    assert not price_matrix(store, "tenant-a", NOW)["rows"]


def test_only_failed_sqs_message_is_retried(store, message):
    import json

    event = {
        "Records": [
            {"messageId": "ok", "body": json.dumps(message)},
            {"messageId": "bad", "body": "not-json"},
        ]
    }
    assert process_batch(event, store, NOW) == {"batchItemFailures": [{"itemIdentifier": "bad"}]}
    assert len(history(store, "tenant-a", "phone", NOW)) == 1


def test_bad_schema_and_row_number_are_not_accepted(store, message):
    for updates in [
        {"schema_version": 2},
        {"schema_version": True},
        {"row_number": True},
        {"row_number": 101},
    ]:
        with pytest.raises((InvalidRecord, AccessDenied)):
            ingest({**message, **updates}, store, NOW)


def test_remapping_after_retry_does_not_duplicate_old_observation(store, message):
    ingest(message, store, NOW)
    store.create_product("tenant-a", "another", "Another variant")
    store.set_mapping("tenant-a", "vendor", "001-PHONE", "another")
    assert ingest(message, store, NOW)["duplicate"]
    assert len(history(store, "tenant-a", "phone", NOW)) == 1
    assert history(store, "tenant-a", "another", NOW) == []


def test_rolling_window_crosses_utc_midnight_and_excludes_exact_end(store, message):
    message["record"]["observed_at"] = "2026-10-18T23:59:59Z"
    ingest(message, store, NOW)
    second = deepcopy(message)
    second["row_number"] = 2
    second["record"]["observed_at"] = "2026-10-19T01:00:00Z"
    ingest(second, store, NOW)
    rows = price_matrix(store, "tenant-a", NOW)["rows"]
    assert len(rows) == 1
    assert rows[0]["observed_at"].startswith("2026-10-18T23:59:59")
