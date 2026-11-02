"""Tests for Notification Lambda, SNS publishing, and deduplication."""

from unittest.mock import MagicMock

from services.reporting.notification import dispatch_notification, send_sns_notification


def test_sns_notification_publish():
    mock_sns = MagicMock()
    success = send_sns_notification(
        topic_arn="arn:aws:sns:us-east-1:123456789012:test-topic",
        subject="Test Alert",
        message="Test message body",
        sns_client=mock_sns,
    )
    assert success is True
    mock_sns.publish.assert_called_once()


def test_notification_deduplication(store):
    mock_sns = MagicMock()
    # Configure tenant with SNS topic
    store.put("tenant-a", "META", {"status": "active", "sns_topic_arn": "arn:aws:sns:us-east-1:123456789012:tenant-a"})

    # First dispatch
    result1 = dispatch_notification(
        tenant_id="tenant-a",
        report_id="rep-100",
        period="2026-10-19",
        summary_text="Daily summary test",
        store=store,
        sns_client=mock_sns,
    )
    assert result1["status"] == "sent"
    assert "sns" in result1["channels"]
    assert result1["sns_sent"] is True
    assert mock_sns.publish.call_count == 1

    # Second dispatch with same report_id
    result2 = dispatch_notification(
        tenant_id="tenant-a",
        report_id="rep-100",
        period="2026-10-19",
        summary_text="Daily summary test",
        store=store,
        sns_client=mock_sns,
    )
    assert result2["status"] == "skipped"
    assert result2["reason"] == "already_sent"
    # SNS publish should not have been called again (zero spam)
    assert mock_sns.publish.call_count == 1


def test_notification_partial_channel_retry(store, monkeypatch):
    mock_sns = MagicMock()
    # Tenant configured with both SNS topic and Slack webhook
    store.put(
        "tenant-a",
        "META",
        {
            "status": "active",
            "sns_topic_arn": "arn:aws:sns:us-east-1:123456789012:tenant-a",
            "slack_webhook_url": "https://hooks.slack.com/services/T00/B00/X00",
        },
    )

    slack_attempts = []

    def mock_slack_fail(url, text, tenant_id, period):
        slack_attempts.append("fail")
        return False

    def mock_slack_success(url, text, tenant_id, period):
        slack_attempts.append("success")
        return True

    # 1. Attempt 1: SNS succeeds, but Slack fails
    monkeypatch.setattr("services.reporting.notification.send_slack_webhook", mock_slack_fail)
    res1 = dispatch_notification(
        tenant_id="tenant-a",
        report_id="rep-200",
        period="2026-10-19",
        summary_text="Daily summary test",
        store=store,
        sns_client=mock_sns,
    )
    assert res1["status"] == "partial"
    assert res1["sns_sent"] is True
    assert res1["slack_sent"] is False
    assert mock_sns.publish.call_count == 1
    assert slack_attempts == ["fail"]

    # Verify state in DynamoDB
    notif_rec = store.get("tenant-a", "NOTIF#rep-200")
    assert notif_rec["sns_sent"] is True
    assert notif_rec["slack_sent"] is False
    assert notif_rec["status"] == "partial"

    # 2. Attempt 2 (Retry): Slack is now healthy and succeeds
    monkeypatch.setattr("services.reporting.notification.send_slack_webhook", mock_slack_success)
    res2 = dispatch_notification(
        tenant_id="tenant-a",
        report_id="rep-200",
        period="2026-10-19",
        summary_text="Daily summary test",
        store=store,
        sns_client=mock_sns,
    )
    # SNS must NOT be called again (avoiding duplicate email to customer)
    assert mock_sns.publish.call_count == 1
    assert slack_attempts == ["fail", "success"]
    assert res2["status"] == "sent"
    assert res2["sns_sent"] is True
    assert res2["slack_sent"] is True

    # 3. Attempt 3: All channels already sent -> should skip
    res3 = dispatch_notification(
        tenant_id="tenant-a",
        report_id="rep-200",
        period="2026-10-19",
        summary_text="Daily summary test",
        store=store,
        sns_client=mock_sns,
    )
    assert res3["status"] == "skipped"
    assert mock_sns.publish.call_count == 1
    assert len(slack_attempts) == 2


def test_notification_none_configured(store):
    store.put("tenant-a", "META", {"status": "active"})
    res = dispatch_notification(
        tenant_id="tenant-a",
        report_id="rep-300",
        period="2026-10-19",
        summary_text="Daily summary test",
        store=store,
    )
    assert res["status"] == "none_configured"
    assert res["sns_sent"] is False
    assert res["slack_sent"] is False


def test_sns_message_carries_tenant_attribute(store):
    mock_sns = MagicMock()
    store.put("tenant-a", "META", {"status": "active", "sns_topic_arn": "arn:aws:sns:us-east-1:123456789012:shared"})
    dispatch_notification("tenant-a", "rep-400", "2026-10-19", "Summary", store, sns_client=mock_sns)
    attributes = mock_sns.publish.call_args.kwargs["MessageAttributes"]
    assert attributes == {"tenant_id": {"DataType": "String", "StringValue": "tenant-a"}}


def test_shared_topic_filter_policy_isolates_tenants():
    """Each tenant's email subscription only matches messages tagged with that tenant."""
    import json

    import boto3
    from moto import mock_aws

    from scripts.subscribe_tenant_email import subscribe

    with mock_aws():
        sns = boto3.client("sns", region_name="ap-southeast-1")
        topic = sns.create_topic(Name="alerts")["TopicArn"]
        subscribe(topic, "tenant-a", "a@example.com", sns)
        subscribe(topic, "tenant-b", "b@example.com", sns)
        subs = sns.list_subscriptions_by_topic(TopicArn=topic)["Subscriptions"]
        policies = {
            s["Endpoint"]: json.loads(sns.get_subscription_attributes(SubscriptionArn=s["SubscriptionArn"])["Attributes"]["FilterPolicy"])
            for s in subs
        }
    assert policies == {"a@example.com": {"tenant_id": ["tenant-a"]}, "b@example.com": {"tenant_id": ["tenant-b"]}}


def test_notification_handler_raises_so_async_invoke_retries(store, monkeypatch):
    import pytest

    from services.reporting.notification import handler

    store.put("tenant-a", "META", {"status": "active", "slack_webhook_url": "https://hooks.slack.com/x"})
    monkeypatch.setattr("services.reporting.notification.send_slack_webhook", lambda *a: False)
    with pytest.raises(RuntimeError):
        handler({"tenant_id": "tenant-a", "report_id": "rep-500", "period": "2026-10-19"}, store=store)


def test_aggregator_hands_off_to_notification_lambda(store, monkeypatch):
    import json

    from services.reporting.aggregator import trigger_notification

    monkeypatch.setenv("NOTIFICATION_FUNCTION_NAME", "notify-fn")
    lam = MagicMock()
    result = trigger_notification("tenant-a", "rep-600", "2026-10-19", "Summary", store, lambda_client=lam)
    assert result == {"status": "queued", "function": "notify-fn"}
    call = lam.invoke.call_args.kwargs
    assert call["FunctionName"] == "notify-fn"
    assert call["InvocationType"] == "Event"
    assert json.loads(call["Payload"])["tenant_id"] == "tenant-a"


def test_aggregator_notifies_in_process_when_invoke_fails(store, monkeypatch):
    from services.reporting.aggregator import trigger_notification

    monkeypatch.setenv("NOTIFICATION_FUNCTION_NAME", "notify-fn")
    lam = MagicMock()
    lam.invoke.side_effect = RuntimeError("throttled")
    store.put("tenant-a", "META", {"status": "active"})
    result = trigger_notification("tenant-a", "rep-700", "2026-10-19", "Summary", store, lambda_client=lam)
    assert result["status"] == "none_configured"
