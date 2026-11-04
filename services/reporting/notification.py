"""Send a stored report through each tenant's SNS and Slack channels."""

import json
import logging
import os
import urllib.request
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import boto3
from botocore.config import Config

from marketdata.contracts import AccessDenied, identifier, iso
from marketdata.repository import DynamoStore
from services.reporting.secrets import secret_field

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def tenant_channel(tenant_id, value, service):
    """Reject a channel belonging to another tenant in a deployed stack."""
    tenant_id = identifier(tenant_id)
    stack_name = os.environ.get("NOTIFICATION_STACK_NAME")
    if not stack_name:
        return value  # Local tests pass their own clients and configuration.
    if not isinstance(value, str):
        raise AccessDenied("invalid_notification_channel")
    parts = value.split(":", 5)
    if (
        len(parts) != 6
        or parts[0] != "arn"
        or parts[2] != service
        or parts[3] != os.environ["AWS_REGION"]
        or parts[4] != os.environ["AWS_ACCOUNT_ID"]
    ):
        raise AccessDenied("notification_channel_not_in_stack")
    if service == "sns":
        valid = parts[5] == f"{stack_name}-tenant-{tenant_id}"
    else:
        valid = parts[5].startswith(f"secret:{stack_name}/tenants/{tenant_id}/slack-")
    if not valid:
        raise AccessDenied("notification_channel_wrong_tenant")
    return value


def send_slack_webhook(webhook_url: str, text: str, tenant_id: str, period: str) -> bool:
    """Send alert message to a tenant's configured Slack webhook."""
    url = urlsplit(webhook_url)
    if (
        url.scheme != "https"
        or url.netloc != "hooks.slack.com"
        or not url.path.startswith("/services/")
        or url.query
        or url.fragment
    ):
        raise ValueError("invalid_slack_webhook")
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
        logger.warning("Slack notification failed for tenant %s (%s)", tenant_id, type(exc).__name__)
        return False


def send_sns_notification(
    topic_arn: str, subject: str, message: str, sns_client=None, tenant_id: str | None = None
) -> bool:
    """Publish to a tenant topic, retaining tenant_id for subscription filters."""
    sns = sns_client or boto3.client("sns", config=Config(
        connect_timeout=3, read_timeout=5,
        retries={"mode": "standard", "total_max_attempts": 2},
    ))
    kwargs = {"TopicArn": topic_arn, "Subject": subject[:100], "Message": message}
    if tenant_id:
        kwargs["MessageAttributes"] = {"tenant_id": {"DataType": "String", "StringValue": tenant_id}}
    try:
        sns.publish(**kwargs)
        return True
    except Exception as exc:
        logger.warning("SNS publish failed (%s)", type(exc).__name__)
        return False


def dispatch_notification(
    tenant_id: str,
    report_id: str,
    period: str,
    summary_text: str,
    store: DynamoStore,
    sns_client=None,
) -> dict:
    """Retry only the tenant channels that have not completed."""
    tenant_id = identifier(tenant_id)
    notif_key = f"NOTIF#{report_id}"
    existing = store.get(tenant_id, notif_key) or {}
    tenant_meta = store.tenant(tenant_id)
    sns_topic = tenant_meta.get("sns_topic_arn")
    slack_secret = tenant_meta.get("slack_secret_arn")
    # Check both destinations before sending to either channel.
    if sns_topic:
        tenant_channel(tenant_id, sns_topic, "sns")
    if slack_secret:
        tenant_channel(tenant_id, slack_secret, "secretsmanager")

    configured = []
    if sns_topic:
        configured.append("sns")
    if slack_secret:
        configured.append("slack")
    if not configured:
        return {"status": "none_configured", "channels": [], "sns_sent": False, "slack_sent": False}

    sns_sent = bool(existing.get("sns_sent") or "sns" in existing.get("channels", []))
    slack_sent = bool(existing.get("slack_sent") or "slack" in existing.get("channels", []))
    sns_error, slack_error = existing.get("sns_error"), existing.get("slack_error")
    created_at = existing.get("created_at", iso(datetime.now(UTC)))
    if (not sns_topic or sns_sent) and (not slack_secret or slack_sent):
        return {
            "status": "skipped", "reason": "already_sent",
            "channels": existing.get("channels", configured),
            "sns_sent": sns_sent, "slack_sent": slack_sent,
        }

    def save_result():
        channels = [name for name, sent in (("sns", sns_sent), ("slack", slack_sent)) if sent]
        status = "sent" if all(name in channels for name in configured) else (
            "partial" if any(name in channels for name in configured) else "failed"
        )
        result = {"status": status, "channels": channels, "sns_sent": sns_sent,
                  "slack_sent": slack_sent, "sns_error": sns_error, "slack_error": slack_error}
        now = datetime.now(UTC)
        store.put(tenant_id, notif_key, {
            **result, "report_id": report_id, "period": period,
            "created_at": created_at,
            "last_attempt_at": iso(now),
            "expires_at": int((now + timedelta(days=90)).timestamp()),
        })
        return result

    subject = f"[MarketData] Báo cáo ngày {period} - {tenant_id}"
    body = f"Báo cáo cho {tenant_id}, ngày {period}.\n\n{summary_text}\n\nXem chi tiết trên Dashboard."
    if sns_topic and not sns_sent:
        sns_sent = send_sns_notification(sns_topic, subject, body, sns_client, tenant_id=tenant_id)
        sns_error = None if sns_sent else "sns_publish_failed"
        if slack_secret:
            # Persist SNS success before a slower Slack request can time out.
            save_result()

    if slack_secret and not slack_sent:
        try:
            webhook = secret_field(slack_secret, "webhook_url")
            slack_sent = send_slack_webhook(webhook, body, tenant_id, period)
            slack_error = None if slack_sent else "slack_webhook_failed"
        except Exception as exc:
            logger.warning("Slack secret/channel unavailable (%s)", type(exc).__name__)
            slack_error = "slack_channel_unavailable"
    return save_result()


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
    if result["status"] in ("failed", "partial"):
        # Async invocations are retried by Lambda; channels already sent are skipped.
        raise RuntimeError(f"notification {result['status']} for {tenant_id}/{report_id}")
    return {"statusCode": 200, "result": result}
