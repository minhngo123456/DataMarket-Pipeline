"""Subscribe every existing active Admin to their tenant's report email, once.

The API keeps this in step for new workspaces and member changes; this script
catches up members that existed before that. It lists what it would do unless
--yes is passed. With --yes, SNS emails each new Admin a confirmation link and
creates a report topic for tenants that have none.

Usage:
    python -m scripts.sync_report_emails --stack marketdata-dev \
        --region ap-southeast-1 [--profile marketdata-dev] [--yes]
"""

import argparse
import os

import boto3
from boto3.dynamodb.conditions import Attr

from marketdata.repository import DynamoStore
from services.api.report_email import sync_report_email


def stack_resource(cfn, stack, logical_id):
    resources = cfn.describe_stack_resources(StackName=stack, LogicalResourceId=logical_id)
    return resources["StackResources"][0]["PhysicalResourceId"]


def active_admins(control):
    kwargs = {"FilterExpression": Attr("sk").begins_with("MEMBER#") & Attr("role").eq("admin")
              & Attr("status").eq("active")}
    while True:
        page = control.scan(**kwargs)
        for item in page.get("Items", []):
            if item["pk"].startswith("T#") and item.get("email"):
                yield item["pk"].removeprefix("T#"), item["email"]
        if "LastEvaluatedKey" not in page:
            return
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stack", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--yes", action="store_true", help="Apply instead of listing")
    args = parser.parse_args()

    boto3.setup_default_session(profile_name=args.profile, region_name=args.region)
    cfn = boto3.client("cloudformation")
    account = boto3.client("sts").get_caller_identity()["Account"]
    key_id = stack_resource(cfn, args.stack, "NotificationKey")
    os.environ.update({
        "NOTIFICATION_STACK_NAME": args.stack,
        "NOTIFICATION_KEY_ARN": f"arn:aws:kms:{args.region}:{account}:key/{key_id}",
        "AWS_REGION": args.region,
        "AWS_ACCOUNT_ID": account,
    })
    store = DynamoStore(stack_resource(cfn, args.stack, "ControlTable"), "unused")

    failed = 0
    for tenant_id, email in sorted(active_admins(store.control)):
        if not args.yes:
            topic = (store.get(tenant_id, "META") or {}).get("sns_topic_arn") or "(topic will be created)"
            print(f"would sync {tenant_id}: {email} -> {topic}")
            continue
        result = sync_report_email(store, tenant_id, email, "admin", "active")
        failed += result == "failed"
        print(f"{tenant_id}: {email} -> {result}")
    if not args.yes:
        print("Dry run only; pass --yes to subscribe.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
