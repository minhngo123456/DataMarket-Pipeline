"""Tenant destinations, channel retries and async report handoff."""

import json
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

from scripts.subscribe_tenant_email import subscribe
from services.reporting.aggregator import trigger_notification
from services.reporting.notification import dispatch_notification, handler, send_sns_notification

STACK = "marketdata-test"
TOPIC_A = "arn:aws:sns:us-east-1:123456789012:marketdata-test-tenant-tenant-a"
TOPIC_B = "arn:aws:sns:us-east-1:123456789012:marketdata-test-tenant-tenant-b"
SLACK_A = "arn:aws:secretsmanager:us-east-1:123456789012:secret:marketdata-test/tenants/tenant-a/slack-abcdef"


@pytest.fixture(autouse=True)
def channel_namespace(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_STACK_NAME", STACK)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCOUNT_ID", "123456789012")


def test_sns_notification_publish():
    sns = MagicMock()
    assert send_sns_notification(TOPIC_A, "Report", "Summary", sns, tenant_id="tenant-a")
    assert sns.publish.call_args.kwargs["MessageAttributes"] == {
        "tenant_id": {"DataType": "String", "StringValue": "tenant-a"}
    }


def test_notification_deduplication(store):
    sns = MagicMock()
    store.put("tenant-a", "META", {"status": "active", "sns_topic_arn": TOPIC_A})
    first = dispatch_notification("tenant-a", "rep-100", "2026-10-19", "Summary", store, sns)
    second = dispatch_notification("tenant-a", "rep-100", "2026-10-19", "Summary", store, sns)
    assert first["status"] == "sent" and first["sns_sent"] is True
    assert second["status"] == "skipped" and second["reason"] == "already_sent"
    assert sns.publish.call_count == 1


def test_notification_partial_channel_retry(store, monkeypatch):
    sns, slack = MagicMock(), MagicMock(side_effect=[False, True])
    store.put("tenant-a", "META", {"status": "active", "sns_topic_arn": TOPIC_A, "slack_secret_arn": SLACK_A})
    monkeypatch.setattr("services.reporting.notification.secret_field", lambda *args: "https://hooks.slack.com/services/test")
    monkeypatch.setattr("services.reporting.notification.send_slack_webhook", slack)

    first = dispatch_notification("tenant-a", "rep-200", "2026-10-19", "Summary", store, sns)
    assert first["status"] == "partial" and first["sns_sent"] and not first["slack_sent"]
    assert store.get("tenant-a", "NOTIF#rep-200")["status"] == "partial"
    created_at = store.get("tenant-a", "NOTIF#rep-200")["created_at"]
    second = dispatch_notification("tenant-a", "rep-200", "2026-10-19", "Summary", store, sns)
    assert second["status"] == "sent" and second["sns_sent"] and second["slack_sent"]
    third = dispatch_notification("tenant-a", "rep-200", "2026-10-19", "Summary", store, sns)
    assert third["status"] == "skipped"
    assert sns.publish.call_count == 1 and slack.call_count == 2
    assert store.get("tenant-a", "NOTIF#rep-200")["created_at"] == created_at


def test_sns_retry_does_not_resend_successful_slack(store, monkeypatch):
    sns = MagicMock()
    sns.publish.side_effect = [TimeoutError("unavailable"), {}]
    slack = MagicMock(return_value=True)
    resolver = MagicMock(return_value="https://hooks.slack.com/services/test")
    store.put("tenant-a", "META", {"status": "active", "sns_topic_arn": TOPIC_A, "slack_secret_arn": SLACK_A})
    monkeypatch.setattr("services.reporting.notification.secret_field", resolver)
    monkeypatch.setattr("services.reporting.notification.send_slack_webhook", slack)
    first = dispatch_notification("tenant-a", "rep-retry", "2026-10-19", "Summary", store, sns)
    second = dispatch_notification("tenant-a", "rep-retry", "2026-10-19", "Summary", store, sns)
    assert first["status"] == "partial" and first["slack_sent"] and not first["sns_sent"]
    assert second["status"] == "sent" and second["sns_error"] is None
    assert sns.publish.call_count == 2 and slack.call_count == 1 and resolver.call_count == 1


def test_sns_success_is_saved_before_slack_secret_is_read(store, monkeypatch, caplog):
    store.put("tenant-a", "META", {"status": "active", "sns_topic_arn": TOPIC_A, "slack_secret_arn": SLACK_A})

    def failed_secret(*args):
        saved = store.get("tenant-a", "NOTIF#rep-secret-failure")
        assert saved["sns_sent"] is True and saved["status"] == "partial"
        raise TimeoutError("sensitive-secret-value")

    monkeypatch.setattr("services.reporting.notification.secret_field", failed_secret)
    result = dispatch_notification("tenant-a", "rep-secret-failure", "2026-10-19", "Summary", store, MagicMock())
    assert result["status"] == "partial" and result["slack_error"] == "slack_channel_unavailable"
    assert "sensitive-secret-value" not in caplog.text
    assert "sensitive-secret-value" not in json.dumps(result)


def test_notification_none_configured(store):
    result = dispatch_notification("tenant-a", "rep-300", "2026-10-19", "Summary", store)
    assert result["status"] == "none_configured"
    assert result["sns_sent"] is False and result["slack_sent"] is False


def test_each_email_subscribes_to_its_tenant_topic():
    with mock_aws():
        sns = boto3.client("sns", region_name="us-east-1")
        for tenant in ("tenant-a", "tenant-b"):
            topic = sns.create_topic(Name=f"{STACK}-tenant-{tenant}")["TopicArn"]
            subscribe(topic, tenant, f"{tenant}@example.com", sns, stack_name=STACK)
            subs = sns.list_subscriptions_by_topic(TopicArn=topic)["Subscriptions"]
            assert [s["Endpoint"] for s in subs] == [f"{tenant}@example.com"]
            attributes = sns.get_subscription_attributes(SubscriptionArn=subs[0]["SubscriptionArn"])["Attributes"]
            assert json.loads(attributes["FilterPolicy"]) == {"tenant_id": [tenant]}


@pytest.mark.parametrize("topic", [TOPIC_B, "arn:aws:sns:us-east-1:123456789012:shared"])
def test_subscribe_rejects_other_tenant_or_shared_topic(topic):
    sns = MagicMock()
    with pytest.raises(ValueError, match="topic_does_not_match_tenant"):
        subscribe(topic, "tenant-a", "a@example.com", sns, stack_name=STACK)
    sns.subscribe.assert_not_called()


def test_notification_handler_raises_so_async_invoke_retries(store, monkeypatch):
    store.put("tenant-a", "META", {"status": "active", "slack_secret_arn": SLACK_A})
    monkeypatch.setattr("services.reporting.notification.secret_field", lambda *args: "https://hooks.slack.com/services/test")
    monkeypatch.setattr("services.reporting.notification.send_slack_webhook", lambda *args: False)
    with pytest.raises(RuntimeError, match="notification failed"):
        handler({"tenant_id": "tenant-a", "report_id": "rep-500", "period": "2026-10-19"}, store=store)


def test_aggregator_hands_off_to_notification_lambda(store, monkeypatch):
    monkeypatch.setenv("NOTIFICATION_FUNCTION_NAME", "notify-fn")
    lam = MagicMock()
    result = trigger_notification("tenant-a", "rep-600", "2026-10-19", "Summary", store, lambda_client=lam)
    assert result == {"status": "queued", "function": "notify-fn"}
    call = lam.invoke.call_args.kwargs
    assert call["FunctionName"] == "notify-fn" and call["InvocationType"] == "Event"
    assert json.loads(call["Payload"])["tenant_id"] == "tenant-a"


def test_failed_handoff_raises_without_sending_inline(store, monkeypatch):
    monkeypatch.setenv("NOTIFICATION_FUNCTION_NAME", "notify-fn")
    inline = MagicMock()
    monkeypatch.setattr("services.reporting.aggregator.dispatch_notification", inline)
    lam = MagicMock()
    lam.invoke.side_effect = RuntimeError("throttled")
    with pytest.raises(RuntimeError, match="throttled"):
        trigger_notification("tenant-a", "rep-700", "2026-10-19", "Summary", store, lambda_client=lam)
    inline.assert_not_called()
