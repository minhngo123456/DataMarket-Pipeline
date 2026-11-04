"""Member transactions and tenant boundaries, exercised without an AWS account."""

import json
from copy import deepcopy

import boto3
import pytest
from botocore.stub import Stubber

from marketdata.access import member
from marketdata.contracts import AccessDenied, Conflict, QuotaExceeded, stable_id
from marketdata.service import ingest
from services.api.app import handler
from services.api.auth import AuthContext
from services.api.routes.members import handle_invite_member
from tests.conftest import NOW
from tests.test_access import add_user
from tests.test_api import make_event


def membership(store, user_id):
    return store.control.get_item(
        Key={"pk": "U#" + user_id, "sk": "MEMBERSHIP"}, ConsistentRead=True,
    ).get("Item")


def test_member_transaction_adopts_legacy_records_and_reinvite_keeps_count(store):
    add_user(store, "legacy-admin", "tenant-a", "admin")
    store.save_member("tenant-a", "new-user", "analyst", "user@example.com")
    assert store.tenant("tenant-a")["member_count"] == 2
    assert membership(store, "new-user")["tenant_id"] == "tenant-a"
    assert member(store, "new-user", ("analyst",)) == "tenant-a"

    store.save_member("tenant-a", "new-user", "viewer", "user@example.com")
    assert store.tenant("tenant-a")["member_count"] == 2
    assert store.get("tenant-a", "MEMBER#new-user")["role"] == "viewer"


def test_inviting_existing_identity_cannot_move_it_to_other_tenant(store):
    store.save_member("tenant-a", "shared-sub", "analyst", "shared@example.com")
    before = membership(store, "shared-sub")
    with pytest.raises(Conflict, match="another_tenant"):
        store.save_member("tenant-b", "shared-sub", "admin", "shared@example.com")
    assert membership(store, "shared-sub") == before
    assert store.get("tenant-b", "MEMBER#shared-sub") is None
    assert member(store, "shared-sub", ("analyst",)) == "tenant-a"


def test_interleaved_invites_cannot_create_sixth_member(store, monkeypatch):
    for i in range(4):
        add_user(store, f"legacy-{i}", "tenant-a", "viewer")
    transact = store.client.transact_write_items
    inserted = False

    def insert_other_invite_before_commit(**kwargs):
        nonlocal inserted
        if not inserted:
            inserted = True
            store.save_member("tenant-a", "winner", "viewer", "winner@example.com")
        return transact(**kwargs)

    monkeypatch.setattr(store.client, "transact_write_items", insert_other_invite_before_commit)
    with pytest.raises(QuotaExceeded, match="member_limit"):
        store.save_member("tenant-a", "loser", "viewer", "loser@example.com")
    assert len(store.members("tenant-a")) == 5
    assert store.tenant("tenant-a")["member_count"] == 5
    assert membership(store, "loser") is None
    assert store.get("tenant-a", "MEMBER#loser") is None


def test_failed_owner_condition_leaves_no_partial_member_or_quota_update(store, monkeypatch):
    before = store.tenant("tenant-a")
    transact = store.client.transact_write_items
    changed = False

    def claim_identity_before_commit(**kwargs):
        nonlocal changed
        if not changed:
            changed = True
            store.control.put_item(Item={
                "pk": "U#racing-sub", "sk": "MEMBERSHIP",
                "tenant_id": "tenant-b", "status": "active",
            })
        return transact(**kwargs)

    monkeypatch.setattr(store.client, "transact_write_items", claim_identity_before_commit)
    with pytest.raises(Conflict, match="another_tenant"):
        store.save_member("tenant-a", "racing-sub", "viewer", "race@example.com")
    assert store.tenant("tenant-a") == before
    assert store.get("tenant-a", "MEMBER#racing-sub") is None
    assert membership(store, "racing-sub")["tenant_id"] == "tenant-b"


def test_disable_and_enable_update_both_records_and_preserve_other_tenants(store):
    add_user(store, "admin-a", "tenant-a", "admin")
    store.save_member("tenant-a", "target", "analyst", "target@example.com")
    add_user(store, "other-user", "tenant-b", "analyst")
    before_other = membership(store, "other-user")
    for status in ("disabled", "active"):
        response = handler(make_event(
            "PATCH", "/members/target", "admin-a", {"status": status},
        ), store=store)
        assert response["statusCode"] == 200
        assert store.get("tenant-a", "MEMBER#target")["status"] == status
        assert membership(store, "target")["status"] == status
        if status == "disabled":
            with pytest.raises(AccessDenied):
                member(store, "target", ("analyst",))
        else:
            assert member(store, "target", ("analyst",)) == "tenant-a"
    assert membership(store, "other-user") == before_other


def test_patch_cannot_update_global_mapping_owned_by_another_tenant(store):
    add_user(store, "admin-a", "tenant-a", "admin")
    add_user(store, "target", "tenant-b", "analyst")
    # A legacy bad row must not let tenant A disable tenant B's actual member.
    store.put("tenant-a", "MEMBER#target", {"role": "viewer", "status": "active"})
    response = handler(make_event(
        "PATCH", "/members/target", "admin-a", {"status": "disabled"},
    ), store=store)
    assert response["statusCode"] == 409
    assert membership(store, "target")["status"] == "active"
    assert store.get("tenant-b", "MEMBER#target")["status"] == "active"
    assert store.get("tenant-a", "MEMBER#target")["status"] == "active"


def test_partial_patch_preserves_a_concurrent_role_change(store, monkeypatch):
    store.save_member("tenant-a", "target", "viewer", "target@example.com")
    transact = store.client.transact_write_items
    changed = False

    def change_role_before_commit(**kwargs):
        nonlocal changed
        if not changed:
            changed = True
            store.save_member("tenant-a", "target", "analyst", None, must_exist=True)
        return transact(**kwargs)

    monkeypatch.setattr(store.client, "transact_write_items", change_role_before_commit)
    result = store.save_member("tenant-a", "target", None, None, "disabled", must_exist=True)
    assert result["role"] == "analyst"
    assert result["status"] == "disabled"
    assert store.get("tenant-a", "MEMBER#target")["role"] == "analyst"


def test_configured_cognito_pool_requires_sub_and_does_not_verify_email(store, monkeypatch):
    monkeypatch.setenv("USER_POOL_ID", "us-east-1_example")
    cognito = boto3.client("cognito-idp")
    with Stubber(cognito) as stub:
        stub.add_response("admin_get_user", {"Username": "new@example.com"}, {
            "UserPoolId": "us-east-1_example", "Username": "new@example.com",
        })
        event = make_event("POST", "/members/invite", "admin-a", {"email": "new@example.com"})
        auth = AuthContext("admin-a", "tenant-a", "admin", {})
        with pytest.raises(Conflict, match="missing_sub"):
            handle_invite_member(event, store, auth, cognito=cognito)
        stub.assert_no_pending_responses()
    assert membership(store, stable_id("member", "new@example.com")) is None
    assert store.members("tenant-a") == []


def test_existing_cognito_email_in_other_tenant_is_not_reassigned(store, monkeypatch):
    monkeypatch.setenv("USER_POOL_ID", "us-east-1_example")
    store.save_member("tenant-b", "existing-sub", "analyst", "existing@example.com")
    cognito = boto3.client("cognito-idp")
    with Stubber(cognito) as stub:
        stub.add_response("admin_get_user", {
            "Username": "existing@example.com",
            "UserAttributes": [{"Name": "sub", "Value": "existing-sub"}],
        }, {"UserPoolId": "us-east-1_example", "Username": "existing@example.com"})
        event = make_event("POST", "/members/invite", "admin-a", {
            "email": "existing@example.com", "role": "admin",
        })
        auth = AuthContext("admin-a", "tenant-a", "admin", {})
        with pytest.raises(Conflict, match="another_tenant"):
            handle_invite_member(event, store, auth, cognito=cognito)
    assert membership(store, "existing-sub")["tenant_id"] == "tenant-b"
    assert store.get("tenant-a", "MEMBER#existing-sub") is None


def test_adding_a_member_can_be_retried_after_the_membership_write_fails(store, monkeypatch):
    monkeypatch.setenv("USER_POOL_ID", "us-east-1_example")
    cognito = boto3.client("cognito-idp")
    auth = AuthContext("admin-a", "tenant-a", "admin", {})
    event = make_event("POST", "/members/invite", "admin-a", {"email": "retry@example.com"})
    save = store.save_member

    def fail_membership(*args, **kwargs):
        raise Conflict("membership_changed_retry_request")

    with Stubber(cognito) as stub:
        stub.add_response("admin_get_user", {
            "Username": "retry@example.com", "UserAttributes": [{"Name": "sub", "Value": "retry-sub"}],
        })
        monkeypatch.setattr(store, "save_member", fail_membership)
        with pytest.raises(Conflict):
            handle_invite_member(event, store, auth, cognito=cognito)
        assert membership(store, "retry-sub") is None
        assert store.get("tenant-a", "MEMBER#retry-sub") is None

        stub.add_response("admin_get_user", {
            "Username": "retry@example.com", "UserAttributes": [{"Name": "sub", "Value": "retry-sub"}],
        })
        monkeypatch.setattr(store, "save_member", save)
        result = handle_invite_member(event, store, auth, cognito=cognito)
        assert result["statusCode"] == 201
        assert member(store, "retry-sub", ("analyst",)) == "tenant-a"
        assert store.tenant("tenant-a")["member_count"] == 1
        stub.assert_no_pending_responses()


def test_two_pending_rows_cannot_map_same_source_sku_to_different_products(store, message):
    message["record"]["source_sku"] = "UNMATCHED"
    first = ingest(message, store, NOW)
    second_message = deepcopy(message)
    second_message["row_number"] = 2
    second = ingest(second_message, store, NOW)
    store.create_product("tenant-a", "tablet", "Tablet")
    store.approve_pending("tenant-a", first["observation_id"], "phone", now=NOW)
    with pytest.raises(Conflict):
        store.approve_pending("tenant-a", second["observation_id"], "tablet", now=NOW)
    assert store.mapping("tenant-a", "vendor", "UNMATCHED")["product_id"] == "phone"
    assert store.receipt("tenant-a", second["observation_id"])["status"] == "pending"
    assert list(store.product_history("tenant-a", "tablet")) == []


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/members/invite", {"email": "new@example.com"}),
    ("PATCH", "/members/target", {"role": "admin"}),
    ("POST", "/jobs", {"source_id": "vendor"}),
])
def test_viewer_cannot_write_members_or_upload(store, method, path, body):
    add_user(store, "viewer-a", "tenant-a", "viewer")
    response = handler(make_event(method, path, "viewer-a", body), store=store)
    assert response["statusCode"] == 403


def test_other_tenants_job_and_report_are_not_visible_even_with_tenant_query(store):
    add_user(store, "admin-b", "tenant-b", "admin")
    store.create_job("tenant-a", "only-a", {"source_id": "vendor", "status": "queued"})
    store.put("tenant-a", "REPORT#secret-report", {
        "report_id": "secret-report", "s3_key": "tenants/tenant-a/reports/secret-report.json",
    })
    for path in ("/jobs/only-a/status", "/reports/secret-report"):
        result = handler(make_event("GET", path, "admin-b", query_params={"tenant_id": "tenant-a"}), store=store)
        assert result["statusCode"] == 404
        assert "download_url" not in json.loads(result["body"])
