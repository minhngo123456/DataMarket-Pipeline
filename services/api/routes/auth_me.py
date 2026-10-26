"""Handler for GET /me."""

from marketdata.repository import DynamoStore
from services.api.auth import AuthContext
from services.api.response import ok


def handle_get_me(event: dict, store: DynamoStore, auth: AuthContext) -> dict:
    """Return identity, current tenant context, and subscription quota limits."""
    tenant_meta = store.tenant(auth.tenant_id)

    return ok({
        "user_id": auth.user_id,
        "tenant_id": auth.tenant_id,
        "role": auth.role,
        "tenant": {
            "name": tenant_meta.get("name", auth.tenant_id),
            "status": tenant_meta.get("status"),
            "product_count": int(tenant_meta.get("product_count", 0)),
            "max_skus": min(int(tenant_meta.get("max_skus", 100)), 100),
            "max_sources": min(int(tenant_meta.get("max_sources", 3)), 3),
        },
        "claims": {k: v for k, v in auth.claims.items() if k in ("sub", "email", "name")},
    })
