"""Subscribe an email address to the shared NotificationTopic for one tenant only.

The stack has a single SNS topic. Every report message carries a ``tenant_id``
attribute, and this script always sets a filter policy on that attribute, so the
address only receives reports of its own tenant. Do not subscribe emails to the
topic without a filter policy (e.g. from the console), or they receive every tenant.

Usage:
    python -m scripts.subscribe_tenant_email --topic-arn <NotificationTopicArn> \
        --tenant-id tenant-a --email ops@example.com [--profile marketdata-dev]
"""

import argparse
import json

import boto3


def filter_policy(tenant_id: str) -> str:
    return json.dumps({"tenant_id": [tenant_id]})


def subscribe(topic_arn: str, tenant_id: str, email: str, sns_client=None) -> str:
    sns = sns_client or boto3.client("sns")
    response = sns.subscribe(
        TopicArn=topic_arn,
        Protocol="email",
        Endpoint=email,
        Attributes={"FilterPolicy": filter_policy(tenant_id), "FilterPolicyScope": "MessageAttributes"},
        ReturnSubscriptionArn=True,
    )
    return response["SubscriptionArn"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--topic-arn", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--region", default="ap-southeast-1")
    args = parser.parse_args()
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    arn = subscribe(args.topic_arn, args.tenant_id, args.email, session.client("sns"))
    print(f"Subscribed {args.email} to tenant {args.tenant_id}: {arn}")
    print("AWS sends a confirmation email; the address receives reports after it is confirmed.")


if __name__ == "__main__":
    main()
