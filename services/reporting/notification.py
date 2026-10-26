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
    """Orchestrate notification delivery across enabled tenant channels with deduplication."""
    # Check deduplication record in DynamoDB
    notif_key = f"NOTIF#{report_id}"
    existing = store.get(tenant_id, notif_key)
    if existing and existing.get("status") == "sent":
        logger.info("Notification for %s/%s already sent. Skipping.", tenant_id, report_id)
        return {"status": "skipped", "reason": "already_sent"}

    tenant_meta = store.tenant(tenant_id)
    subject = f"[MarketData] Báo cáo thị trường ngày {period} - {tenant_id}"
    digest_body = (
        f"Kính gửi Quý khách hàng ({tenant_id}),\n\n"
        f"Báo cáo thị trường 24 giờ cho ngày {period} đã được tạo thành công.\n\n"
        f"--- TÓM TẮT ĐIỀU HÀNH ---\n"
        f"{summary_text}\n\n"
        f"Để xem ma trận chi tiết và biểu đồ biến động giá, vui lòng truy cập Dashboard.\n"
    )

    channels_sent = []

    # 1. AWS SNS Topic
    sns_topic = tenant_meta.get("sns_topic_arn") or os.environ.get("SNS_TOPIC_ARN")
    if sns_topic:
        if send_sns_notification(sns_topic, subject, digest_body, sns_client=sns_client):
            channels_sent.append("sns")

    # 2. Slack Webhook
    slack_webhook = tenant_meta.get("slack_webhook_url") or os.environ.get("SLACK_WEBHOOK_URL")
    if slack_webhook:
        if send_slack_webhook(slack_webhook, digest_body, tenant_id, period):
            channels_sent.append("slack")

    # Save notification record in DynamoDB ControlTable
    now = datetime.now(UTC)
    store.put(
        tenant_id,
        notif_key,
        {
            "report_id": report_id,
            "period": period,
            "status": "sent" if channels_sent else "none_configured",
            "channels": channels_sent,
            "sent_at": iso(now),
            "expires_at": int((now + timedelta(days=90)).timestamp()),
        },
    )

    return {
        "status": "sent" if channels_sent else "none_configured",
        "channels": channels_sent,
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
