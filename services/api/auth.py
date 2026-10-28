"""Authentication, JWT Claims Extraction, and RBAC Authorization.

Connects AWS Cognito JWT Authorizer claims to the multi-tenant membership
and role-based access control (RBAC) enforced in DynamoDB ControlTable.
"""

import os
from dataclasses import dataclass
from typing import Sequence

from marketdata.access import member
from marketdata.contracts import AccessDenied, identifier
from marketdata.repository import DynamoStore


@dataclass
class AuthContext:
    user_id: str
    tenant_id: str
    role: str
    claims: dict


def extract_claims(event: dict) -> dict:
    """Extract Cognito claims from API Gateway HTTP API requestContext.

    In production, API Gateway JWT Authorizer populates:
    event['requestContext']['authorizer']['jwt']['claims']
    For local development/tests, Authorization header fallback is only enabled
    when ALLOW_TEST_AUTH=true (as specified in Report 2 section 2.6).
    """
    rc = event.get("requestContext", {})
    authorizer = rc.get("authorizer", {})

    # 1. API Gateway HTTP API JWT authorizer path
    jwt_claims = authorizer.get("jwt", {}).get("claims")
    if jwt_claims and isinstance(jwt_claims, dict):
        return jwt_claims

    # 2. Direct lambda authorizer / test context fallback
    if "claims" in authorizer and isinstance(authorizer["claims"], dict):
        return authorizer["claims"]

    # 3. Test/Dev header fallback (strictly guarded by ALLOW_TEST_AUTH flag)
    if os.environ.get("ALLOW_TEST_AUTH", "false").lower() == "true":
        headers = event.get("headers") or {}
        test_sub = headers.get("x-test-sub") or headers.get("X-Test-Sub")
        if test_sub:
            return {"sub": test_sub, "email": f"{test_sub}@example.com"}

        auth_header = headers.get("authorization") or headers.get("Authorization") or ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            # In test environments, if token matches a simple user id:
            if token and " " not in token:
                return {"sub": token}

    return {}


def authenticate_and_authorize(
    event: dict,
    store: DynamoStore,
    allowed_roles: Sequence[str] = ("admin", "analyst", "viewer"),
) -> AuthContext:
    """Verify authenticated identity and assert RBAC permissions against DynamoDB.

    Raises:
        AccessDenied: If user is not authenticated, membership is inactive, or role disallowed.
    """
    claims = extract_claims(event)
    user_id = claims.get("sub")
    if not user_id:
        raise AccessDenied("unauthenticated_missing_sub")

    user_id = identifier(user_id)

    # 1. Check membership and active role via marketdata/access.py
    tenant_id = member(store, user_id, allowed_roles)

    # 2. Fetch specific role to return in AuthContext
    member_rec = store.get(tenant_id, "MEMBER#" + user_id)
    role = member_rec.get("role", "viewer") if member_rec else "viewer"

    return AuthContext(
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        claims=claims,
    )
