"""Integration and unit tests for API Lambda, RBAC, and Quota enforcement."""

import json

from services.api.app import handler
from tests.test_access import add_user


def make_event(
    method: str,
    path: str,
    user_id: str | None = None,
    body: dict | None = None,
    query_params: dict | None = None,
) -> dict:
    event = {
        "rawPath": path,
        "requestContext": {
            "http": {"method": method},
            "authorizer": {
                "jwt": {
                    "claims": {"sub": user_id, "email": f"{user_id}@example.com"} if user_id else {}
                }
            },
        },
        "queryStringParameters": query_params or {},
    }
    if body is not None:
        event["body"] = json.dumps(body)
    return event


def test_unauthenticated_request_rejected(store):
    event = make_event("GET", "/me")
    resp = handler(event, store=store)
    assert resp["statusCode"] == 403
    data = json.loads(resp["body"])
    assert data["error"]["code"] == "access_denied"


def test_inactive_membership_rejected(store):
    add_user(store, "user-revoked", "tenant-a", "analyst")
    # Revoke user
    store.put("tenant-a", "MEMBER#user-revoked", {"role": "analyst", "status": "disabled"})

    event = make_event("GET", "/me", user_id="user-revoked")
    resp = handler(event, store=store)
    assert resp["statusCode"] == 403


def test_get_me_returns_tenant_and_quotas(store):
    add_user(store, "analyst-a", "tenant-a", "analyst")
    event = make_event("GET", "/me", user_id="analyst-a")
    resp = handler(event, store=store)

    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert data["user_id"] == "analyst-a"
    assert data["tenant_id"] == "tenant-a"
    assert data["role"] == "analyst"
    assert data["tenant"]["max_skus"] == 100
    assert data["tenant"]["max_sources"] == 3


def test_rbac_viewer_cannot_modify_catalog(store):
    add_user(store, "viewer-a", "tenant-a", "viewer")

    # Viewer can read catalog
    resp = handler(make_event("GET", "/catalog", user_id="viewer-a"), store=store)
    assert resp["statusCode"] == 200

    # Viewer cannot add product
    resp = handler(
        make_event("POST", "/catalog", user_id="viewer-a", body={"product_id": "p2", "name": "Tablet"}),
        store=store,
    )
    assert resp["statusCode"] == 403


def test_analyst_can_create_product(store):
    add_user(store, "analyst-a", "tenant-a", "analyst")
    resp = handler(
        make_event("POST", "/catalog", user_id="analyst-a", body={"product_id": "tablet", "name": "Tablet Pro"}),
        store=store,
    )
    assert resp["statusCode"] == 201
    data = json.loads(resp["body"])
    assert data["product_id"] == "tablet"

    # Verify it exists in GET /catalog
    resp = handler(make_event("GET", "/catalog", user_id="analyst-a"), store=store)
    catalog = json.loads(resp["body"])
    product_ids = [p["product_id"] for p in catalog["products"]]
    assert "tablet" in product_ids


def test_catalog_quota_limit_enforced(store):
    add_user(store, "admin-a", "tenant-a", "admin")
    # Set quota to 1 and product_count to 1
    store.put("tenant-a", "META", {"status": "active", "product_count": 1, "max_skus": 1})

    resp = handler(
        make_event("POST", "/catalog", user_id="admin-a", body={"product_id": "excess", "name": "Excess SKU"}),
        store=store,
    )
    assert resp["statusCode"] == 429
    data = json.loads(resp["body"])
    assert data["error"]["code"] == "quota_exceeded"


def test_source_quota_limit_3_sources(store):
    add_user(store, "admin-a", "tenant-a", "admin")
    # tenant-a already has 'vendor'
    resp = handler(
        make_event("POST", "/sources", user_id="admin-a", body={"source_id": "s2", "kind": "api"}),
        store=store,
    )
    assert resp["statusCode"] == 201

    resp = handler(
        make_event("POST", "/sources", user_id="admin-a", body={"source_id": "s3", "kind": "web"}),
        store=store,
    )
    assert resp["statusCode"] == 201

    # 4th source must exceed quota
    resp = handler(
        make_event("POST", "/sources", user_id="admin-a", body={"source_id": "s4", "kind": "csv"}),
        store=store,
    )
    assert resp["statusCode"] == 429
    data = json.loads(resp["body"])
    assert data["error"]["code"] == "quota_exceeded"


def test_csv_upload_url_generation(store, monkeypatch):
    monkeypatch.setenv("RAW_BUCKET", "raw-bucket-test")
    add_user(store, "analyst-a", "tenant-a", "analyst")

    event = make_event("POST", "/jobs", user_id="analyst-a", body={"source_id": "vendor"})
    resp = handler(event, store=store)

    assert resp["statusCode"] in (200, 201)
    data = json.loads(resp["body"])
    assert data["tenant_id"] == "tenant-a"
    assert "job_id" in data
    assert "upload_url" in data
    assert f"tenants/tenant-a/raw/{data['job_id']}" in data["upload_url"]

    # Test GET /jobs/:id/status
    job_id = data["job_id"]
    status_resp = handler(make_event("GET", f"/jobs/{job_id}/status", user_id="analyst-a"), store=store)
    assert status_resp["statusCode"] == 200
    status_data = json.loads(status_resp["body"])
    assert status_data["job_id"] == job_id
    assert status_data["status"] == "queued"


def test_dashboard_stats_and_alerts(store):
    add_user(store, "viewer-a", "tenant-a", "viewer")

    # GET /dashboard/stats
    resp = handler(make_event("GET", "/dashboard/stats", user_id="viewer-a"), store=store)
    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert "total_skus" in data
    assert "total_sources" in data

    # GET /alerts
    alert_resp = handler(make_event("GET", "/alerts", user_id="viewer-a"), store=store)
    assert alert_resp["statusCode"] == 200
    alert_data = json.loads(alert_resp["body"])
    assert "data" in alert_data


def test_sources_patch_and_delete(store):
    add_user(store, "admin-a", "tenant-a", "admin")

    # PATCH /sources/:id
    patch_resp = handler(
        make_event("PATCH", "/sources/vendor", user_id="admin-a", body={"enabled": False}),
        store=store,
    )
    assert patch_resp["statusCode"] == 200
    assert json.loads(patch_resp["body"])["enabled"] is False

    # DELETE /sources/:id
    del_resp = handler(make_event("DELETE", "/sources/vendor", user_id="admin-a"), store=store)
    assert del_resp["statusCode"] == 200
    assert json.loads(del_resp["body"])["status"] == "deleted"


def test_members_management_and_quota(store):
    add_user(store, "admin-a", "tenant-a", "admin")

    # GET /members
    resp = handler(make_event("GET", "/members", user_id="admin-a"), store=store)
    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert data["max_members"] == 5

    # Invite members up to quota 5
    for i in range(1, 5):
        resp = handler(
            make_event("POST", "/members/invite", user_id="admin-a", body={"email": f"u{i}@example.com", "role": "viewer"}),
            store=store,
        )
        assert resp["statusCode"] == 201

    # 6th member must exceed quota
    resp = handler(
        make_event("POST", "/members/invite", user_id="admin-a", body={"email": "overflow@example.com", "role": "viewer"}),
        store=store,
    )
    assert resp["statusCode"] == 429
    assert json.loads(resp["body"])["error"]["code"] == "quota_exceeded"


def test_cross_tenant_isolation(store):
    add_user(store, "analyst-a", "tenant-a", "analyst")
    add_user(store, "analyst-b", "tenant-b", "analyst")

    # Add product in tenant-a
    handler(
        make_event("POST", "/catalog", user_id="analyst-a", body={"product_id": "secret-a", "name": "Secret A"}),
        store=store,
    )

    # Analyst in tenant-b cannot see secret-a
    resp = handler(make_event("GET", "/catalog", user_id="analyst-b"), store=store)
    data = json.loads(resp["body"])
    product_ids = [p["product_id"] for p in data["products"]]
    assert "secret-a" not in product_ids
