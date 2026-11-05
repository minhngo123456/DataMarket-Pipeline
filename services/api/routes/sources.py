"""Handler for /sources endpoints."""

import json
from datetime import UTC, datetime
from urllib.parse import urlparse

from marketdata.contracts import AccessDenied, QuotaExceeded, identifier, iso
from marketdata.repository import DynamoStore
from services.api.auth import AuthContext
from services.api.response import error, ok, paginated

MASKED_HEADER_VALUE = "***"
PUBLIC_COLLECTION_FIELDS = (
    "collection_status", "last_job_id", "last_attempt_at", "last_success_at",
    "last_success_job_id", "last_success_job_at", "last_error_code",
)


def is_http_url(value) -> bool:
    """Workers fetch this with urlopen, which also opens file:// and ftp://, so allow HTTP(S) only."""
    try:
        parsed = urlparse(value) if isinstance(value, str) else None
        return bool(parsed and parsed.scheme in ("http", "https") and parsed.hostname)
    except ValueError:
        return False


def visible_headers(headers: dict, auth: AuthContext) -> dict:
    """Header values can hold API keys, so only Admin reads them back."""
    if auth.role == "admin":
        return headers
    return {name: MASKED_HEADER_VALUE for name in headers}


def restore_masked_headers(headers: dict, stored: dict | None) -> dict:
    """A client echoing the mask keeps the stored value; a mask with nothing stored is dropped."""
    stored = stored or {}
    restored = {}
    for name, value in headers.items():
        if value != MASKED_HEADER_VALUE:
            restored[name] = value
        elif name in stored:
            restored[name] = stored[name]
    return restored


def handle_get_sources(event: dict, store: DynamoStore, auth: AuthContext) -> dict:
    """List all configured data sources for the current tenant."""
    store.tenant(auth.tenant_id)
    raw_sources = list(store.sources(auth.tenant_id))

    data = []
    for s in raw_sources:
        item = {
            "source_id": s.get("source_id") or s["sk"].removeprefix("SOURCE#"),
            "kind": s.get("kind"),
            "enabled": bool(s.get("enabled", True)),
            "name": s.get("name", s.get("source_id")),
            "updated_at": s.get("updated_at"),
        }
        item.update({field: s[field] for field in PUBLIC_COLLECTION_FIELDS if s.get(field) is not None})
        endpoint = s.get("endpoint") or s.get("url")
        url = s.get("url") or s.get("endpoint")
        if endpoint:
            item["endpoint"] = endpoint
        if url:
            item["url"] = url
        if "headers" in s and s["headers"] is not None:
            item["headers"] = visible_headers(s["headers"], auth)
        data.append(item)

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

    endpoint = body.get("endpoint") or body.get("url")
    url = body.get("url") or body.get("endpoint")
    headers = body.get("headers")

    if (endpoint and not is_http_url(endpoint)) or (url and not is_http_url(url)):
        return error("invalid_source_payload", "endpoint/url must be an http(s) URL", 400)

    if headers is not None and not isinstance(headers, dict):
        return error(
            "invalid_source_payload",
            "headers must be a JSON object/dict if provided",
            400,
        )

    source_id = identifier(source_id)
    current = store.get(auth.tenant_id, "SOURCE#" + source_id)
    if headers is not None:
        headers = restore_masked_headers(headers, (current or {}).get("headers"))
    tenant_meta = store.tenant(auth.tenant_id)
    max_sources = min(int(tenant_meta.get("max_sources", 3)), 3)

    # Check quota against currently enabled sources
    existing = list(store.sources(auth.tenant_id))
    if len(existing) >= max_sources and not any(
        (s.get("source_id") or s["sk"].removeprefix("SOURCE#")) == source_id for s in existing
    ):
        raise QuotaExceeded("source_limit_reached")

    now = datetime.now(UTC)
    source_item = {
        "source_id": source_id,
        "kind": kind,
        "name": name,
        "enabled": bool(enabled),
        "updated_at": iso(now),
        # POST replaces the supported configuration, never worker-owned health.
        "endpoint": str(endpoint) if endpoint else None,
        "url": str(url) if url else None,
        "headers": headers,
    }
    store.update_source_configuration(
        auth.tenant_id, source_id, source_item, previous=current,
    )

    resp_data = {
        "source_id": source_id,
        "kind": kind,
        "name": name,
        "enabled": bool(enabled),
        "status": "configured",
    }
    if endpoint:
        resp_data["endpoint"] = str(endpoint)
    if url:
        resp_data["url"] = str(url)
    if headers is not None:
        resp_data["headers"] = visible_headers(headers, auth)

    return ok(
        resp_data,
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

    if "headers" in body and body["headers"] is not None and not isinstance(body["headers"], dict):
        return error(
            "invalid_source_payload",
            "headers must be a JSON object/dict if provided",
            400,
        )

    now = datetime.now(UTC)
    updated = {
        "name": body.get("name", existing.get("name")),
        "enabled": bool(body.get("enabled", existing.get("enabled", True))),
        "updated_at": iso(now),
    }

    if "endpoint" in body or "url" in body:
        new_endpoint = body.get("endpoint") or body.get("url")
        new_url = body.get("url") or body.get("endpoint")
        if (new_endpoint and not is_http_url(new_endpoint)) or (new_url and not is_http_url(new_url)):
            return error("invalid_source_payload", "endpoint/url must be an http(s) URL", 400)
        if new_endpoint:
            updated["endpoint"] = str(new_endpoint)
        if new_url:
            updated["url"] = str(new_url)

    if "headers" in body:
        if body["headers"] is None:
            updated["headers"] = None
        else:
            updated["headers"] = restore_masked_headers(body["headers"], existing.get("headers"))

    updated = store.update_source_configuration(
        auth.tenant_id, source_id, updated, previous=existing,
    )

    resp_data = {
        "source_id": source_id,
        "name": updated.get("name"),
        "enabled": updated.get("enabled"),
        "status": "updated",
    }
    if "endpoint" in updated:
        resp_data["endpoint"] = updated["endpoint"]
    if "url" in updated:
        resp_data["url"] = updated["url"]
    if "headers" in updated and updated["headers"] is not None:
        resp_data["headers"] = visible_headers(updated["headers"], auth)

    return ok(resp_data)


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
