"""Handler for /members and /members/invite endpoints."""

import json

from boto3.dynamodb.conditions import Key

from marketdata.contracts import AccessDenied, QuotaExceeded, identifier
from marketdata.repository import DynamoStore, pk
from services.api.auth import AuthContext
from services.api.response import error, ok


def handle_get_members(event: dict, store: DynamoStore, auth: AuthContext) -> dict:
    """List all members in the current tenant (Admin only)."""
    if auth.role != "admin":
        raise AccessDenied("admin_role_required")

    store.tenant(auth.tenant_id)
    records = list(store.pages(
        store.control,
        KeyConditionExpression=Key("pk").eq(pk(auth.tenant_id)) & Key("sk").begins_with("MEMBER#"),
        ConsistentRead=True,
    ))

    data = [
        {
            "user_id": r["sk"].removeprefix("MEMBER#"),
            "role": r.get("role", "viewer"),
            "status": r.get("status", "active"),
            "email": r.get("email"),
        }
        for r in records
    ]

    return ok({
        "data": data,
        "total": len(data),
        "max_members": 5,
    })


def handle_invite_member(event: dict, store: DynamoStore, auth: AuthContext) -> dict:
    """Invite/add a new member, enforcing max 5 members quota (Admin only)."""
    if auth.role != "admin":
        raise AccessDenied("admin_role_required")

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return error("invalid_json", "Request body must be valid JSON", 400)

    email = body.get("email")
    role = body.get("role", "analyst")
    user_id = body.get("user_id") or (email.split("@")[0] if email and "@" in email else None)

    if not email or role not in ("admin", "analyst", "viewer") or not user_id:
        return error("invalid_member_payload", "email and valid role ('admin', 'analyst', 'viewer') are required", 400)

    user_id = identifier(user_id)

    # Check member quota (max 5)
    existing_members = list(store.pages(
        store.control,
        KeyConditionExpression=Key("pk").eq(pk(auth.tenant_id)) & Key("sk").begins_with("MEMBER#"),
        ConsistentRead=True,
    ))
    if len(existing_members) >= 5 and not any(m["sk"] == "MEMBER#" + user_id for m in existing_members):
        raise QuotaExceeded("member_limit_reached")

    # Add user membership in ControlTable
    store.control.put_item(
        Item={"pk": "U#" + user_id, "sk": "MEMBERSHIP", "tenant_id": auth.tenant_id, "status": "active"}
    )
    store.put(
        auth.tenant_id,
        "MEMBER#" + user_id,
        {"role": role, "status": "active", "email": email},
    )

    return ok(
        {
            "user_id": user_id,
            "email": email,
            "role": role,
            "status": "active",
            "message": "Đã thêm thành viên thành công.",
        },
        status_code=201,
    )
