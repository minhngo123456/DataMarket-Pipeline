"""Handler for /members endpoints."""

import json
import os

import boto3
from boto3.dynamodb.conditions import Key

from marketdata.contracts import AccessDenied, Conflict, QuotaExceeded, identifier, stable_id
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


def cognito_user_id(email: str, cognito=None) -> str | None:
    """Create (or look up) the Cognito user for email and return its sub.

    Cognito sub is what the JWT authorizer passes as identity, so membership must be
    keyed by it. Returns None when no user pool is configured (local tests).
    """
    pool_id = os.environ.get("USER_POOL_ID")
    if not pool_id:
        return None
    client = cognito or boto3.client("cognito-idp")
    try:
        user = client.admin_create_user(
            UserPoolId=pool_id,
            Username=email,
            UserAttributes=[
                {"Name": "email", "Value": email},
            ],
            DesiredDeliveryMediums=["EMAIL"],
        )["User"]
        attributes = user.get("Attributes", [])
    except client.exceptions.UsernameExistsException:
        attributes = client.admin_get_user(UserPoolId=pool_id, Username=email).get("UserAttributes", [])
    user_id = next((a["Value"] for a in attributes if a["Name"] == "sub"), None)
    if not user_id:
        raise Conflict("cognito_user_missing_sub")
    return user_id


def handle_invite_member(event: dict, store: DynamoStore, auth: AuthContext, cognito=None) -> dict:
    """Invite/add a new member, enforcing max 5 members quota (Admin only)."""
    if auth.role != "admin":
        raise AccessDenied("admin_role_required")

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return error("invalid_json", "Request body must be valid JSON", 400)

    if not isinstance(body, dict) or not isinstance(body.get("email"), str):
        return error("invalid_member_payload", "email and a valid role are required", 400)
    email = body["email"].strip().lower()
    role = body.get("role", "analyst")

    if "@" not in email or role not in ("admin", "analyst", "viewer"):
        return error("invalid_member_payload", "email and valid role ('admin', 'analyst', 'viewer') are required", 400)

    store.tenant(auth.tenant_id)

    # Check member quota (max 5)
    existing_members = list(store.pages(
        store.control,
        KeyConditionExpression=Key("pk").eq(pk(auth.tenant_id)) & Key("sk").begins_with("MEMBER#"),
        ConsistentRead=True,
    ))
    already_member = next((m for m in existing_members if m.get("email") == email), None)
    if len(existing_members) >= 5 and not already_member:
        raise QuotaExceeded("member_limit_reached")

    # Membership must be keyed by the Cognito sub, the identity the JWT authorizer passes.
    # Without a user pool (local tests) fall back to a stable id derived from the email.
    user_id = cognito_user_id(email, cognito) or (
        already_member["sk"].removeprefix("MEMBER#") if already_member else stable_id("member", email)
    )
    user_id = identifier(user_id)

    # If Cognito succeeds but this transaction fails, the user has no application
    # membership. Retrying the invitation looks up that Cognito user again.
    store.save_member(auth.tenant_id, user_id, role, email)

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

    if not isinstance(body, dict):
        return error("invalid_member_payload", "Request body must be an object", 400)

    new_role = body.get("role")
    new_status = body.get("status")

    if not new_role and not new_status:
        return error("missing_update_fields", "At least 'role' or 'status' must be provided", 400)

    if new_role and new_role not in ("admin", "analyst", "viewer"):
        return error("invalid_role", "role must be one of: admin, analyst, viewer", 400)

    if new_status and new_status not in ("active", "disabled"):
        return error("invalid_status", "status must be one of: active, disabled", 400)

    updated_rec = store.save_member(
        auth.tenant_id, user_id, new_role, None, new_status, must_exist=True,
    )

    return ok({
        "user_id": user_id,
        "email": updated_rec.get("email"),
        "role": updated_rec["role"],
        "status": updated_rec["status"],
        "message": "Cập nhật vai trò thành viên thành công.",
    })
