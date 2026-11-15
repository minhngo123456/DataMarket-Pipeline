"""Tenant-scoped history and price matrix for the API/reporting owner."""

from datetime import timedelta
from decimal import Decimal

from marketdata.contracts import InvalidRecord


def history(store, tenant_id, product_id, now, days=30):
    if type(days) is not int or not 1 <= days <= 30:
        raise InvalidRecord("history_days_must_be_1_to_30")
    store.product(tenant_id, product_id)
    start = now - timedelta(days=days)
    return [
        r
        for r in store.product_history(tenant_id, product_id)
        if start <= store.observed(r) < now and int(r["expires_at"]) > now.timestamp()
    ]


def price_matrix(store, tenant_id, now, reference_source="internal", basis="tax_included"):
    if basis not in ("tax_included", "tax_excluded"):
        raise InvalidRecord("explicit_price_basis_required")
    start = now - timedelta(hours=24)
    # Deleting a source keeps its price history but removes it from comparisons at once.
    configured = {item["sk"].removeprefix("SOURCE#") for item in store.sources(tenant_id)}
    latest = {}
    for row in store.window(tenant_id, start, now):
        if row["price_basis"] != basis or int(row["expires_at"]) <= now.timestamp():
            continue
        if row["source_id"] not in configured:
            continue
        key = (row["product_id"], row["source_id"])
        if key not in latest or (row["observed_at"], row["observation_id"]) > (
            latest[key]["observed_at"],
            latest[key]["observation_id"],
        ):
            latest[key] = row
    rows = []
    for (product_id, source_id), row in sorted(latest.items()):
        reference = latest.get((product_id, reference_source))
        change = None
        if reference and reference["price"] > 0:
            change = ((row["price"] - reference["price"]) / reference["price"] * 100).quantize(
                Decimal("0.01")
            )
        rows.append(
            {
                "product_id": product_id,
                "source_id": source_id,
                "price": row["price"],
                "observed_at": row["observed_at"],
                "change_percent": change,
            }
        )
    return {
        "tenant_id": tenant_id,
        "window_start": start.isoformat(),
        "window_end": now.isoformat(),
        "price_basis": basis,
        "rows": rows,
    }
