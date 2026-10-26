"""Handler for GET /history/{product_id} and /products/{product_id}/price-history."""

from datetime import UTC, datetime

from marketdata.access import read_history
from marketdata.contracts import identifier
from marketdata.repository import DynamoStore
from services.api.auth import AuthContext
from services.api.response import error, ok


def handle_get_history(event: dict, store: DynamoStore, auth: AuthContext) -> dict:
    """Retrieve up to 30-day historical price observations for a specific product."""
    path_params = event.get("pathParameters") or {}
    product_id = path_params.get("product_id") or path_params.get("id")
    if not product_id:
        return error("missing_product_id", "product_id path parameter is required", 400)

    product_id = identifier(product_id)

    query_params = event.get("queryStringParameters") or {}
    try:
        days = int(query_params.get("days", 30))
    except ValueError:
        return error("invalid_days", "days parameter must be an integer between 1 and 30", 400)

    now = datetime.now(UTC)
    records = read_history(store, auth.user_id, product_id, now=now, days=days)

    observations = [
        {
            "observation_id": r["observation_id"],
            "source_id": r["source_id"],
            "source_sku": r.get("source_sku"),
            "price": r["price"],
            "currency": r["currency"],
            "price_basis": r.get("price_basis"),
            "observed_at": r["observed_at"],
        }
        for r in records
    ]

    return ok({
        "product_id": product_id,
        "days": days,
        "data": observations,
        "observations": observations,
        "total": len(observations),
        "count": len(observations),
        "tenant_id": auth.tenant_id,
    })
