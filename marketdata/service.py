"""Normalizer business rules; AWS calls are kept in the repository."""

from datetime import timedelta

from marketdata.contracts import InvalidRecord, envelope, stable_id
from marketdata.normalize import normalize


def ingest(value, store, now):
    value = envelope(value)
    _, source, _ = store.require_job(value)
    row_id = stable_id(value["tenant_id"], value["source_id"], value["job_id"], value["row_number"])
    previous = store.receipt(value["tenant_id"], row_id)
    if previous:
        return {"observation_id": row_id, "status": previous["status"], "duplicate": True}
    receipt = {
        "schema_version": 1,
        "tenant_id": value["tenant_id"],
        "job_id": value["job_id"],
        "source_id": value["source_id"],
        "observation_id": row_id,
        "row_number": value["row_number"],
        "expires_at": int((now + timedelta(days=30)).timestamp()),
    }
    try:
        normalized = normalize(value["record"], source, now)
    except InvalidRecord as exc:
        receipt.update(status="rejected", reason=str(exc))
        saved = store.save_receipt(receipt)
        if not saved:
            return {
                "observation_id": row_id,
                "status": store.receipt(value["tenant_id"], row_id)["status"],
                "duplicate": True,
            }
        return {"observation_id": row_id, "status": "rejected", "reason": str(exc)}
    receipt.update(normalized)
    mapping = store.mapping(value["tenant_id"], value["source_id"], normalized["source_sku"])
    if mapping is None:
        receipt.update(status="pending", reason="sku_needs_confirmation")
        saved = store.save_receipt(receipt)
    else:
        store.product(value["tenant_id"], mapping["product_id"])
        receipt.update(status="accepted", product_id=mapping["product_id"])
        saved = store.save_observation(receipt)
    if not saved:
        return {
            "observation_id": row_id,
            "status": store.receipt(value["tenant_id"], row_id)["status"],
            "duplicate": True,
        }
    return {"observation_id": row_id, "status": receipt["status"], "duplicate": False}
