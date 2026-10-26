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
    # SNS publish should not have been called again
    assert mock_sns.publish.call_count == 1
