"""Subscribe an email address to the named tenant's SNS report topic."""

import argparse
import json
import re
import sys

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from marketdata.contracts import identifier


def filter_policy(tenant_id: str) -> str:
    return json.dumps({"tenant_id": [tenant_id]})


def subscribe(topic_arn: str, tenant_id: str, email: str, sns_client=None, *, stack_name: str) -> str:
    tenant_id = identifier(tenant_id)
    parts = topic_arn.split(":", 5)
    if (
        not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,127}", stack_name)
        or len(parts) != 6 or parts[0] != "arn" or parts[2] != "sns"
        or parts[5] != f"{stack_name}-tenant-{tenant_id}"
    ):
        raise ValueError("topic_does_not_match_tenant")
    sns = sns_client or boto3.client("sns", config=Config(
        connect_timeout=3, read_timeout=5,
        retries={"mode": "standard", "total_max_attempts": 2},
    ))
    response = sns.subscribe(
        TopicArn=topic_arn,
        Protocol="email",
        Endpoint=email,
        Attributes={"FilterPolicy": filter_policy(tenant_id), "FilterPolicyScope": "MessageAttributes"},
        ReturnSubscriptionArn=True,
    )
    return response["SubscriptionArn"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--topic-arn", required=True)
    parser.add_argument("--stack", required=True, help="Main pipeline stack name")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    try:
        arn = subscribe(args.topic_arn, args.tenant_id, args.email, session.client("sns"), stack_name=args.stack)
    except (BotoCoreError, ClientError, ValueError) as exc:
        print(f"Subscription failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(f"Subscription requested: {arn}")
    print("Confirm the SNS email before testing report delivery.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
