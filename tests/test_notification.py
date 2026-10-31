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
