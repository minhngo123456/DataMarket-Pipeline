"""Handler for /sources endpoints."""

import json
from datetime import UTC, datetime

from marketdata.contracts import AccessDenied, QuotaExceeded, identifier, iso
from marketdata.repository import DynamoStore
from services.api.auth import AuthContext
from services.api.response import error, ok, paginated


def handle_get_sources(event: dict, store: DynamoStore, auth: AuthContext) -> dict:
    """List all configured data sources for the current tenant."""
    store.tenant(auth.tenant_id)
    raw_sources = list(store.sources(auth.tenant_id))

    data = [
        {
            "source_id": s.get("source_id") or s["sk"].removeprefix("SOURCE#"),
            "kind": s.get("kind"),
            "enabled": bool(s.get("enabled", True)),
            "name": s.get("name", s.get("source_id")),
            "updated_at": s.get("updated_at"),
        }
        for s in raw_sources
    ]

    return paginated(data=data, total=len(data), page=1, limit=20, extra={"sources": data})


def handle_post_sources(event: dict, store: DynamoStore, auth: AuthContext) -> dict:
    """Configure a new source, enforcing max 3 sources quota per tenant (Admin & Analyst)."""
    if auth.role not in ("admin", "analyst"):
        raise AccessDenied("admin_or_analyst_role_required")

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return error("invalid_json", "Request body must be valid JSON", 400)

    source_id = body.get("source_id")
    kind = body.get("kind")
    name = body.get("name", source_id)
    enabled = body.get("enabled", True)

    if not source_id or kind not in ("csv", "api", "web"):
        return error(
            "invalid_source_payload",
            "source_id is required and kind must be one of: csv, api, web",
            400,
        )

    source_id = identifier(source_id)
    tenant_meta = store.tenant(auth.tenant_id)
    max_sources = min(int(tenant_meta.get("max_sources", 3)), 3)

    # Check quota against currently enabled sources
    existing = list(store.sources(auth.tenant_id))
    if len(existing) >= max_sources and not any(
        (s.get("source_id") or s["sk"].removeprefix("SOURCE#")) == source_id for s in existing
    ):
        raise QuotaExceeded("source_limit_reached")

    now = datetime.now(UTC)
    store.put(
        auth.tenant_id,
        "SOURCE#" + source_id,
        {
            "source_id": source_id,
            "kind": kind,
            "name": name,
            "enabled": bool(enabled),
            "updated_at": iso(now),
        },
    )

    return ok(
        {
            "source_id": source_id,
            "kind": kind,
            "name": name,
            "enabled": bool(enabled),
            "status": "configured",
        },
        status_code=201,
    )


def handle_patch_source(event: dict, store: DynamoStore, auth: AuthContext) -> dict:
    """Update or toggle enabled status for a source (Admin & Analyst)."""
    if auth.role not in ("admin", "analyst"):
        raise AccessDenied("admin_or_analyst_role_required")

    path_params = event.get("pathParameters") or {}
    source_id = path_params.get("source_id")
    if not source_id:
        return error("missing_source_id", "source_id is required", 400)

    source_id = identifier(source_id)
    store.tenant(auth.tenant_id)
    existing = store.get(auth.tenant_id, "SOURCE#" + source_id)
    if not existing:
        return error("source_not_found", "Source does not exist", 404)

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return error("invalid_json", "Request body must be valid JSON", 400)

    now = datetime.now(UTC)
    updated = {
        **existing,
        "name": body.get("name", existing.get("name")),
        "enabled": bool(body.get("enabled", existing.get("enabled", True))),
        "updated_at": iso(now),
    }
    store.put(auth.tenant_id, "SOURCE#" + source_id, updated)

    return ok({
        "source_id": source_id,
        "name": updated.get("name"),
        "enabled": updated.get("enabled"),
        "status": "updated",
    })


def handle_delete_source(event: dict, store: DynamoStore, auth: AuthContext) -> dict:
    """Delete a configured data source (Admin only)."""
    if auth.role != "admin":
        raise AccessDenied("admin_role_required")

    path_params = event.get("pathParameters") or {}
    source_id = path_params.get("source_id")
    if not source_id:
        return error("missing_source_id", "source_id is required", 400)

    source_id = identifier(source_id)
    store.tenant(auth.tenant_id)
    existing = store.get(auth.tenant_id, "SOURCE#" + source_id)
    if not existing:
        return error("source_not_found", "Source does not exist", 404)

    # Delete source record from ControlTable
    from marketdata.repository import pk
    store.control.delete_item(Key={"pk": pk(auth.tenant_id), "sk": "SOURCE#" + source_id})

    return ok({
        "source_id": source_id,
        "status": "deleted",
    })
