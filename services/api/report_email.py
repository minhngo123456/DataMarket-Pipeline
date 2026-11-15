"""Keep each tenant's report email list in step with its active Admins.

Every active Admin is subscribed to the tenant's SNS report topic; the topic is
created on first need. Anyone who stops being an active Admin is unsubscribed.
SNS still sends its own confirmation email, which the recipient must click.
Failures are logged and never block the membership change that triggered them.
"""

import json
import logging
import os

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from marketdata.contracts import AccessDenied, Conflict, identifier
from services.reporting.notification import tenant_channel

logger = logging.getLogger(__name__)

PENDING = "PendingConfirmation"


def enabled() -> bool:
    """Only a deployed stack names the topic namespace and key; local runs do nothing."""
    return bool(os.environ.get("NOTIFICATION_STACK_NAME") and os.environ.get("NOTIFICATION_KEY_ARN"))


def sns_client():
    return boto3.client("sns", config=Config(
        connect_timeout=3, read_timeout=5,
        retries={"mode": "standard", "total_max_attempts": 2},
    ))


def topic_policy(topic_arn: str) -> str:
    return json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Sid": "RequireTLSForPublishAndSubscribe",
            "Effect": "Deny",
            "Principal": "*",
            "Action": ["sns:Publish", "sns:Subscribe"],
            "Resource": topic_arn,
            "Condition": {"Bool": {"aws:SecureTransport": "false"}},
        }],
    })


def report_topic(store, tenant_id: str, sns, *, create: bool) -> str | None:
    """Return the tenant's topic ARN, creating and recording it when asked."""
    meta = store.get(tenant_id, "META") or {}
    existing = meta.get("sns_topic_arn")
    if existing:
        return tenant_channel(tenant_id, existing, "sns")
    if not create:
        return None
    name = f"{os.environ['NOTIFICATION_STACK_NAME']}-tenant-{identifier(tenant_id)}"
    # CreateTopic is idempotent for the same name and attributes.
    arn = sns.create_topic(
        Name=name,
        Attributes={"KmsMasterKeyId": os.environ["NOTIFICATION_KEY_ARN"], "SignatureVersion": "2"},
        Tags=[{"Key": "tenant_id", "Value": tenant_id}],
    )["TopicArn"]
    sns.set_topic_attributes(TopicArn=arn, AttributeName="Policy", AttributeValue=topic_policy(arn))
    arn = tenant_channel(tenant_id, arn, "sns")
    store.set_report_topic(tenant_id, arn)
    return arn


def subscriptions(sns, topic_arn: str, email: str) -> list[str]:
    found = []
    for page in sns.get_paginator("list_subscriptions_by_topic").paginate(TopicArn=topic_arn):
        for sub in page.get("Subscriptions", []):
            if sub.get("Protocol") == "email" and sub.get("Endpoint", "").strip().lower() == email:
                found.append(sub["SubscriptionArn"])
    return found


def sync_report_email(store, tenant_id: str, email, role, status, sns=None) -> str | None:
    """Subscribe an active Admin's email, or unsubscribe anyone else's.

    Returns a short status for the API response, or None when not deployed.
    """
    if not enabled() or not isinstance(email, str) or "@" not in email:
        return None
    email = email.strip().lower()
    wanted = role == "admin" and status == "active"
    sns = sns or sns_client()
    try:
        topic_arn = report_topic(store, tenant_id, sns, create=wanted)
        if not topic_arn:
            return "not_subscribed"
        current = subscriptions(sns, topic_arn, email)
        if wanted:
            if current:
                return "pending_confirmation" if PENDING in current else "subscribed"
            sns.subscribe(
                TopicArn=topic_arn,
                Protocol="email",
                Endpoint=email,
                Attributes={
                    "FilterPolicy": json.dumps({"tenant_id": [tenant_id]}),
                    "FilterPolicyScope": "MessageAttributes",
                },
            )
            return "confirmation_sent"
        for arn in current:
            # A pending subscription has no ARN to remove; SNS drops it after three days.
            if arn != PENDING:
                sns.unsubscribe(SubscriptionArn=arn)
        return "unsubscribed" if current else "not_subscribed"
    except (BotoCoreError, ClientError, AccessDenied, Conflict) as exc:
        logger.warning("Report email sync failed for tenant (%s)", type(exc).__name__)
        return "failed"
