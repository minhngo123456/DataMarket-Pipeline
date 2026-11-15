"""Active Admins are kept on their tenant's report email list automatically."""

import json

import boto3
import pytest

from services.api.app import handler
from services.api.auth import AuthContext
from services.api.report_email import sync_report_email
from services.api.routes.members import handle_invite_member, handle_patch_member
from tests.test_access import add_user
from tests.test_api import make_event

ACCOUNT = "123456789012"


@pytest.fixture
def deployed(store, monkeypatch):
    for name, value in {
        "NOTIFICATION_STACK_NAME": "stack",
        "NOTIFICATION_KEY_ARN": f"arn:aws:kms:us-east-1:{ACCOUNT}:key/abc",
        "AWS_REGION": "us-east-1",
        "AWS_ACCOUNT_ID": ACCOUNT,
    }.items():
        monkeypatch.setenv(name, value)
    return store


def emails(topic_arn):
    subs = boto3.client("sns").list_subscriptions_by_topic(TopicArn=topic_arn)["Subscriptions"]
    return sorted(s["Endpoint"] for s in subs)


def topic_of(store, tenant):
    return store.get(tenant, "META").get("sns_topic_arn")


def test_new_workspace_creator_gets_a_topic_and_a_subscription(deployed):
    event = make_event("POST", "/tenants", "owner", {"name": "Shop"})
    event["requestContext"]["authorizer"]["jwt"]["claims"].update(email="Owner@Example.com", email_verified="true")
    response = handler(event, store=deployed)
    body = json.loads(response["body"])

    assert response["statusCode"] == 201
    assert body["report_email"] == "confirmation_sent"
    arn = topic_of(deployed, body["tenant_id"])
    assert arn == f"arn:aws:sns:us-east-1:{ACCOUNT}:stack-tenant-{body['tenant_id']}"
    assert emails(arn) == ["owner@example.com"]
    attributes = boto3.client("sns").get_topic_attributes(TopicArn=arn)["Attributes"]
    assert attributes["KmsMasterKeyId"].endswith("key/abc")
    assert "aws:SecureTransport" in attributes["Policy"]


def test_sync_is_idempotent_and_keeps_the_tenant_filter(deployed):
    assert sync_report_email(deployed, "tenant-a", "a@example.com", "admin", "active") == "confirmation_sent"
    assert sync_report_email(deployed, "tenant-a", "A@Example.com ", "admin", "active") in (
        "subscribed", "pending_confirmation",
    )
    arn = topic_of(deployed, "tenant-a")
    assert emails(arn) == ["a@example.com"]
    sub = boto3.client("sns").list_subscriptions_by_topic(TopicArn=arn)["Subscriptions"][0]
    attrs = boto3.client("sns").get_subscription_attributes(SubscriptionArn=sub["SubscriptionArn"])["Attributes"]
    assert json.loads(attrs["FilterPolicy"]) == {"tenant_id": ["tenant-a"]}


def test_non_admin_never_creates_a_topic(deployed):
    assert sync_report_email(deployed, "tenant-a", "v@example.com", "viewer", "active") == "not_subscribed"
    assert topic_of(deployed, "tenant-a") is None


def admin_patch(store, user, body):
    event = make_event("PATCH", f"/members/{user}", "admin-a", body)
    event["pathParameters"] = {"user_id": user}
    return handle_patch_member(event, store, AuthContext("admin-a", "tenant-a", "admin", {}))


def test_promotion_subscribes_and_demotion_or_disable_unsubscribes(deployed):
    add_user(deployed, "member-b", "tenant-a", "viewer")
    deployed.put("tenant-a", "MEMBER#member-b", {"role": "viewer", "status": "active", "email": "b@example.com"})

    assert json.loads(admin_patch(deployed, "member-b", {"role": "admin"})["body"])["report_email"] == (
        "confirmation_sent"
    )
    arn = topic_of(deployed, "tenant-a")
    assert emails(arn) == ["b@example.com"]

    assert json.loads(admin_patch(deployed, "member-b", {"status": "disabled"})["body"])["report_email"] == (
        "unsubscribed"
    )
    assert emails(arn) == []


def test_invited_admin_is_subscribed(deployed):
    event = make_event("POST", "/members/invite", "admin-a", {"email": "New@Example.com", "role": "admin"})
    response = handle_invite_member(event, deployed, AuthContext("admin-a", "tenant-a", "admin", {}))
    assert response["statusCode"] == 201
    assert json.loads(response["body"])["report_email"] == "confirmation_sent"
    assert emails(topic_of(deployed, "tenant-a")) == ["new@example.com"]


def test_topic_from_another_tenant_is_refused_without_sending(deployed):
    deployed.control.update_item(
        Key={"pk": "T#tenant-a", "sk": "META"},
        UpdateExpression="SET sns_topic_arn = :a",
        ExpressionAttributeValues={":a": f"arn:aws:sns:us-east-1:{ACCOUNT}:stack-tenant-tenant-b"},
    )
    assert sync_report_email(deployed, "tenant-a", "a@example.com", "admin", "active") == "failed"


def test_local_runs_do_nothing(store):
    assert sync_report_email(store, "tenant-a", "a@example.com", "admin", "active") is None
    assert topic_of(store, "tenant-a") is None
