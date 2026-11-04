"""Self-service workspace creation must never reach an existing tenant."""

import json

import boto3
import pytest
from botocore.stub import Stubber

from marketdata.access import member
from marketdata.contracts import Conflict
from services.api.app import handler
from services.api.auth import AuthContext
from services.api.routes.members import handle_invite_member
from tests.test_access import add_user
from tests.test_api import make_event


def signup_event(body, user="new-user", email="New.User@Example.com", verified="true"):
    event = make_event("POST", "/tenants", user, body)
    event["requestContext"]["authorizer"]["jwt"]["claims"].update(email=email, email_verified=verified)
    return event


def create(store, *args, **kwargs):
    response = handler(signup_event(*args, **kwargs), store=store)
    return response["statusCode"], json.loads(response["body"])


def test_signed_up_user_becomes_admin_of_a_new_tenant_only(store):
    status, body = create(store, {"name": "  Shop ABC  "})

    assert status == 201
    assert body["role"] == "admin" and body["name"] == "Shop ABC"
    assert member(store, "new-user", ("admin",)) == body["tenant_id"]
    meta = store.tenant(body["tenant_id"])
    assert meta["name"] == "Shop ABC"
    assert (meta["max_skus"], meta["max_sources"], meta["member_count"]) == (100, 3, 1)
    assert store.get(body["tenant_id"], "MEMBER#new-user")["email"] == "new.user@example.com"
    assert store.get("tenant-a", "MEMBER#new-user") is None


def test_new_tenant_ids_ignore_client_input_so_existing_tenants_cannot_be_targeted(store):
    before = store.tenant("tenant-a")
    status, body = create(store, {
        "name": "Takeover", "tenant_id": "tenant-a", "pk": "T#tenant-a", "role": "viewer", "status": "disabled",
    })

    assert status == 201
    assert body["tenant_id"] != "tenant-a" and body["tenant_id"].startswith("t-")
    assert body["role"] == "admin"
    assert store.tenant("tenant-a") == before
    assert store.get("tenant-a", "MEMBER#new-user") is None


def test_generated_id_colliding_with_a_tenant_changes_nothing(store):
    before = store.tenant("tenant-a")
    with pytest.raises(Conflict):
        store.create_tenant("tenant-a", "new-user", "Takeover", "new@example.com")

    assert store.tenant("tenant-a") == before
    assert store.get("tenant-a", "MEMBER#new-user") is None
    assert "Item" not in store.control.get_item(Key={"pk": "U#new-user", "sk": "MEMBERSHIP"})


def test_one_tenant_per_user(store):
    assert create(store, {"name": "First"})[0] == 201
    status, body = create(store, {"name": "Second"})
    assert status == 409
    assert body["error"]["message"] == "user_already_has_membership"


def test_existing_member_cannot_create_a_tenant(store):
    add_user(store, "viewer-a", "tenant-a", "viewer")
    status, _ = create(store, {"name": "Escape"}, user="viewer-a")
    assert status == 409
    assert member(store, "viewer-a", ("viewer",)) == "tenant-a"


def test_disabled_member_cannot_escape_by_creating_a_tenant(store):
    store.save_member("tenant-a", "locked", "analyst", "locked@example.com")
    store.save_member("tenant-a", "locked", None, None, "disabled", must_exist=True)
    status, _ = create(store, {"name": "Escape"}, user="locked")
    assert status == 409
    assert store.control.get_item(Key={"pk": "U#locked", "sk": "MEMBERSHIP"})["Item"]["tenant_id"] == "tenant-a"


@pytest.mark.parametrize("claims", [
    {"verified": "false"},
    {"verified": None},
    {"email": ""},
])
def test_unverified_email_is_refused(store, claims):
    status, body = create(store, {"name": "Shop"}, **claims)
    assert status == 403
    assert body["error"]["message"] == "verified_email_required"


def test_anonymous_request_is_refused(store):
    response = handler(make_event("POST", "/tenants", None, {"name": "Shop"}), store=store)
    assert response["statusCode"] == 403


@pytest.mark.parametrize("name", ["", " ", "x", "y" * 81, 123, None, "bad\nname"])
def test_invalid_names_are_refused(store, name):
    status, body = create(store, {"name": name})
    assert status == 400
    assert body["error"]["code"] == "invalid_tenant_name"
    assert "Item" not in store.control.get_item(Key={"pk": "U#new-user", "sk": "MEMBERSHIP"})


def test_user_without_membership_stays_locked_out_of_other_routes(store):
    for method, path in (("GET", "/me"), ("GET", "/sources"), ("GET", "/members"), ("POST", "/members/invite")):
        response = handler(make_event(method, path, "new-user", {}), store=store)
        assert response["statusCode"] == 403
        assert json.loads(response["body"])["error"]["message"] == "membership_unavailable"


def invite(store, cognito, email="joiner@example.com", role="viewer"):
    event = make_event("POST", "/members/invite", "admin-a", {"email": email, "role": role})
    return handle_invite_member(event, store, AuthContext("admin-a", "tenant-a", "admin", {}), cognito=cognito)


def test_admin_adds_a_confirmed_user_without_sending_anything(store, monkeypatch):
    monkeypatch.setenv("USER_POOL_ID", "us-east-1_example")
    cognito = boto3.client("cognito-idp")
    with Stubber(cognito) as stub:
        stub.add_response("admin_get_user", {
            "Username": "joiner@example.com", "UserStatus": "CONFIRMED",
            "UserAttributes": [{"Name": "sub", "Value": "joiner-sub"}],
        }, {"UserPoolId": "us-east-1_example", "Username": "joiner@example.com"})
        assert invite(store, cognito)["statusCode"] == 201
        stub.assert_no_pending_responses()
    assert member(store, "joiner-sub", ("viewer",)) == "tenant-a"


def test_unknown_email_is_not_created_or_invited(store, monkeypatch):
    monkeypatch.setenv("USER_POOL_ID", "us-east-1_example")
    cognito = boto3.client("cognito-idp")
    with Stubber(cognito) as stub:
        stub.add_client_error("admin_get_user", service_error_code="UserNotFoundException")
        response = invite(store, cognito)
    assert response["statusCode"] == 404
    assert json.loads(response["body"])["error"]["code"] == "user_not_registered"
    assert store.members("tenant-a") == []


def test_unconfirmed_sign_up_cannot_claim_a_membership(store, monkeypatch):
    # Anyone can start a sign-up with someone else's address.
    monkeypatch.setenv("USER_POOL_ID", "us-east-1_example")
    cognito = boto3.client("cognito-idp")
    with Stubber(cognito) as stub:
        stub.add_response("admin_get_user", {
            "Username": "joiner@example.com", "UserStatus": "UNCONFIRMED",
            "UserAttributes": [{"Name": "sub", "Value": "squatter-sub"}],
        })
        response = invite(store, cognito)
    assert response["statusCode"] == 404
    assert store.get("tenant-a", "MEMBER#squatter-sub") is None


def test_user_who_created_a_tenant_cannot_be_moved_by_another_admin(store, monkeypatch):
    _, body = create(store, {"name": "Mine"}, user="owner-sub")
    monkeypatch.setenv("USER_POOL_ID", "us-east-1_example")
    cognito = boto3.client("cognito-idp")
    with Stubber(cognito) as stub:
        stub.add_response("admin_get_user", {
            "Username": "joiner@example.com", "UserStatus": "CONFIRMED",
            "UserAttributes": [{"Name": "sub", "Value": "owner-sub"}],
        })
        with pytest.raises(Conflict, match="another_tenant"):
            invite(store, cognito)
    assert member(store, "owner-sub", ("admin",)) == body["tenant_id"]
