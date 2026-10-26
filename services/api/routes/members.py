"""Handler for /members endpoints."""

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


def handle_patch_member(event: dict, store: DynamoStore, auth: AuthContext) -> dict:
    """Update role or status for an existing tenant member (Admin only)."""
    if auth.role != "admin":
        raise AccessDenied("admin_role_required")

    path_params = event.get("pathParameters") or {}
    user_id = path_params.get("user_id") or path_params.get("id")
    if not user_id:
        return error("missing_user_id", "user_id is required", 400)

    user_id = identifier(user_id)
    store.tenant(auth.tenant_id)

    member_rec = store.get(auth.tenant_id, "MEMBER#" + user_id)
    if not member_rec:
        return error("member_not_found", "Member does not exist in this tenant", 404)

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return error("invalid_json", "Request body must be valid JSON", 400)

    new_role = body.get("role")
    new_status = body.get("status")

    if not new_role and not new_status:
        return error("missing_update_fields", "At least 'role' or 'status' must be provided", 400)

    if new_role and new_role not in ("admin", "analyst", "viewer"):
        return error("invalid_role", "role must be one of: admin, analyst, viewer", 400)

    if new_status and new_status not in ("active", "disabled"):
        return error("invalid_status", "status must be one of: active, disabled", 400)

    updated_rec = {
        **member_rec,
        "role": new_role if new_role else member_rec.get("role", "viewer"),
        "status": new_status if new_status else member_rec.get("status", "active"),
    }

    # Update tenant-side record
    store.put(auth.tenant_id, "MEMBER#" + user_id, updated_rec)

    # If status changed, also update user membership in ControlTable for immediate revocation
    if new_status:
        user_membership = store.control.get_item(
            Key={"pk": "U#" + user_id, "sk": "MEMBERSHIP"}
        ).get("Item")
        if user_membership:
            store.control.put_item(
                Item={**user_membership, "status": new_status}
            )

    return ok({
        "user_id": user_id,
        "email": updated_rec.get("email"),
        "role": updated_rec["role"],
        "status": updated_rec["status"],
        "message": "Cập nhật vai trò thành viên thành công.",
    })
