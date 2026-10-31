"""Notification Lambda and Dispatcher for Tenant Price Alerts.

Handles multichannel alerting via AWS SNS (Email digests) and Slack Webhooks,
with built-in deduplication against DynamoDB ControlTable.
"""

import json
import logging
import os
import urllib.request
from datetime import UTC, datetime, timedelta

import boto3

from marketdata.contracts import iso
from marketdata.repository import DynamoStore

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def send_slack_webhook(webhook_url: str, text: str, tenant_id: str, period: str) -> bool:
    """Send alert message to a tenant's configured Slack webhook."""
    payload = {
        "text": f"*{tenant_id} - Báo cáo Thị trường ngày {period}*\n\n{text[:1500]}",
    }
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as exc:
        logger.warning("Slack notification failed for tenant %s: %s", tenant_id, exc)
        return False


def send_sns_notification(topic_arn: str, subject: str, message: str, sns_client=None) -> bool:
    """Publish notification message to AWS SNS topic."""
    sns = sns_client or boto3.client("sns")
    try:
        sns.publish(
            TopicArn=topic_arn,
            Subject=subject[:100],
            Message=message,
        )
        return True
    except Exception as exc:
        logger.warning("SNS publish failed for topic %s: %s", topic_arn, exc)
        return False


def dispatch_notification(
    tenant_id: str,
    report_id: str,
    period: str,
    summary_text: str,
    store: DynamoStore,
    sns_client=None,
) -> dict:
    """Orchestrate notification delivery across enabled tenant channels with per-channel deduplication and retry."""
    notif_key = f"NOTIF#{report_id}"
    existing = store.get(tenant_id, notif_key) or {}

    tenant_meta = store.tenant(tenant_id)
    sns_topic = tenant_meta.get("sns_topic_arn") or os.environ.get("SNS_TOPIC_ARN")
    slack_webhook = tenant_meta.get("slack_webhook_url") or os.environ.get("SLACK_WEBHOOK_URL")

    # Determine which channels were already successfully sent in a prior attempt
    sns_already_sent = bool(existing.get("sns_sent", False) or "sns" in existing.get("channels", []))
    slack_already_sent = bool(existing.get("slack_sent", False) or "slack" in existing.get("channels", []))

    configured_channels = []
    if sns_topic:
        configured_channels.append("sns")
    if slack_webhook:
        configured_channels.append("slack")

    if not configured_channels:
        return {"status": "none_configured", "channels": [], "sns_sent": False, "slack_sent": False}

    # If all configured channels have already succeeded, skip entirely
    all_configured_sent = (not sns_topic or sns_already_sent) and (not slack_webhook or slack_already_sent)
    if all_configured_sent:
        logger.info(
            "Notification for %s/%s already delivered to all configured channels. Skipping.",
            tenant_id,
            report_id,
        )
        return {
            "status": "skipped",
            "reason": "already_sent",
            "channels": existing.get("channels", configured_channels),
            "sns_sent": sns_already_sent,
            "slack_sent": slack_already_sent,
        }

    subject = f"[MarketData] Báo cáo thị trường ngày {period} - {tenant_id}"
    digest_body = (
        f"Kính gửi Quý khách hàng ({tenant_id}),\n\n"
        f"Báo cáo thị trường 24 giờ cho ngày {period} đã được tạo thành công.\n\n"
        f"--- TÓM TẮT ĐIỀU HÀNH ---\n"
        f"{summary_text}\n\n"
        f"Để xem ma trận chi tiết và biểu đồ biến động giá, vui lòng truy cập Dashboard.\n"
    )

    sns_sent = sns_already_sent
    slack_sent = slack_already_sent
    sns_error = existing.get("sns_error")
    slack_error = existing.get("slack_error")

    # 1. AWS SNS Topic: only send if enabled and not already delivered
    if sns_topic:
        if not sns_already_sent:
            if send_sns_notification(sns_topic, subject, digest_body, sns_client=sns_client):
                sns_sent = True
                sns_error = None
            else:
                sns_sent = False
                sns_error = "sns_publish_failed"
        else:
            logger.info("SNS notification already sent for %s/%s. Skipping to prevent duplicate email.", tenant_id, report_id)

    # 2. Slack Webhook: only send if enabled and not already delivered
    if slack_webhook:
        if not slack_already_sent:
            if send_slack_webhook(slack_webhook, digest_body, tenant_id, period):
                slack_sent = True
                slack_error = None
            else:
                slack_sent = False
                slack_error = "slack_webhook_failed"
        else:
            logger.info("Slack notification already sent for %s/%s. Skipping to prevent duplicate message.", tenant_id, report_id)

    channels_sent = []
    if sns_sent:
        channels_sent.append("sns")
    if slack_sent:
        channels_sent.append("slack")

    # Determine overall status
    if all(c in channels_sent for c in configured_channels):
        overall_status = "sent"
    elif any(c in channels_sent for c in configured_channels):
        overall_status = "partial"
    else:
        overall_status = "failed"

    now = datetime.now(UTC)
    record = {
        "report_id": report_id,
        "period": period,
        "status": overall_status,
        "channels": channels_sent,
        "sns_sent": sns_sent,
        "slack_sent": slack_sent,
        "sns_error": sns_error,
        "slack_error": slack_error,
        "last_attempt_at": iso(now),
        "expires_at": int((now + timedelta(days=90)).timestamp()),
    }
    if "created_at" in existing:
        record["created_at"] = existing["created_at"]
    else:
        record["created_at"] = iso(now)

    store.put(tenant_id, notif_key, record)

    return {
        "status": overall_status,
        "channels": channels_sent,
        "sns_sent": sns_sent,
        "slack_sent": slack_sent,
        "sns_error": sns_error,
        "slack_error": slack_error,
    }


def handler(event: dict, context=None, store: DynamoStore | None = None) -> dict:
    """AWS Lambda entry point for direct notification invocations."""
    if store is None:
        store = DynamoStore()

    tenant_id = event.get("tenant_id")
    report_id = event.get("report_id")
    period = event.get("period", "Unknown")
    summary = event.get("summary", "")

    if not tenant_id or not report_id:
        return {"statusCode": 400, "error": "tenant_id and report_id required"}

    result = dispatch_notification(tenant_id, report_id, period, summary, store)
    return {"statusCode": 200, "result": result}
