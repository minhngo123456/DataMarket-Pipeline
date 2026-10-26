"""Handler for pending SKU review and approval endpoints."""

import json
from datetime import UTC, datetime

from marketdata.access import approve_pending
from marketdata.repository import DynamoStore
from services.api.auth import AuthContext
from services.api.response import error, ok, paginated


def handle_get_pending_skus(event: dict, store: DynamoStore, auth: AuthContext) -> dict:
    """Retrieve list of unmapped/unmatched SKUs awaiting Analyst review."""
    store.tenant(auth.tenant_id)
    receipts = store.receipts(auth.tenant_id, status="pending")

    items = [
        {
            "observation_id": r["observation_id"],
            "source_id": r["source_id"],
            "source_sku": r["source_sku"],
            "price": r["price"],
            "currency": r["currency"],
            "price_basis": r.get("price_basis"),
            "observed_at": r["observed_at"],
            "job_id": r["job_id"],
            "reason": r.get("reason"),
            "expires_at": int(r.get("expires_at", 0)),
        }
        for r in receipts
    ]

    return paginated(
        data=items,
        total=len(items),
        page=1,
        limit=20,
        extra={"tenant_id": auth.tenant_id, "count": len(items), "items": items},
    )


def handle_approve_pending_sku(event: dict, store: DynamoStore, auth: AuthContext) -> dict:
    """Approve a pending SKU to map to a tenant catalog product via DynamoDB transaction."""
    path_params = event.get("pathParameters") or {}

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return error("invalid_json", "Request body must be valid JSON", 400)

    observation_id = body.get("observation_id") or path_params.get("id")
    product_id = body.get("product_id") or path_params.get("id") or path_params.get("product_id")
    action = body.get("action", "approve")

    if not observation_id or not product_id:
        return error("missing_required_fields", "observation_id and product_id are required", 400)

    now = datetime.now(UTC)
    approve_pending(store, auth.user_id, observation_id, product_id, now=now)

    return ok({
        "observation_id": observation_id,
        "product_id": product_id,
        "status": "approved",
        "action": action,
        "message": "Ghép SKU thành công, quy tắc mapping đã được ghi nhận cho các lần import sau.",
        "tenant_id": auth.tenant_id,
        "approved_at": now.isoformat(),
    })
