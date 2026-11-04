"""Handler for POST /tenants: a signed-up user without a membership creates their workspace."""

import json
import uuid

from marketdata.contracts import AccessDenied
from marketdata.repository import DynamoStore
from services.api.auth import extract_claims
from services.api.response import error, ok

MIN_NAME_LENGTH = 2
MAX_NAME_LENGTH = 80


def handle_create_tenant(event: dict, store: DynamoStore) -> dict:
    """Make the caller the Admin of a new tenant; the server chooses the id and the role."""
    claims = extract_claims(event)
    user_id = claims.get("sub")
    if not user_id:
        raise AccessDenied("unauthenticated_missing_sub")
    email = str(claims.get("email") or "").strip().lower()
    if not email or str(claims.get("email_verified")).lower() != "true":
        raise AccessDenied("verified_email_required")

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return error("invalid_json", "Request body must be valid JSON", 400)

    name = body.get("name") if isinstance(body, dict) else None
    name = name.strip() if isinstance(name, str) else ""
    if not MIN_NAME_LENGTH <= len(name) <= MAX_NAME_LENGTH or not name.isprintable():
        return error(
            "invalid_tenant_name",
            f"Tên workspace phải có từ {MIN_NAME_LENGTH} đến {MAX_NAME_LENGTH} ký tự.",
            400,
        )

    tenant_id = "t-" + uuid.uuid4().hex
    store.create_tenant(tenant_id, user_id, name, email)
    return ok({"tenant_id": tenant_id, "name": name, "role": "admin", "status": "active"}, status_code=201)
