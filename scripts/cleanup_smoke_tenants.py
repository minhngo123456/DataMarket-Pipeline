"""Remove tenants left behind by scripts/smoke_test_aws.py from a live stack.

Prints what would be deleted unless --yes is passed. Only tenant ids shaped exactly
like the smoke test's are touched, so real tenants can never match. S3 buckets are
versioned; --yes removes every version of the objects, which cannot be undone.

Usage:
    python -m scripts.cleanup_smoke_tenants --stack marketdata-dev \
        --region ap-southeast-1 [--profile marketdata-dev] [--yes]
"""

import argparse
import re
from collections import defaultdict

import boto3
from boto3.dynamodb.conditions import Attr

# Older smoke runs used 8 hex characters; scripts/smoke_test_aws.py now uses 12.
SMOKE_TENANT = re.compile(r"^smoke-[ab]-(?:[0-9a-f]{8}|[0-9a-f]{12})$")
TENANT_PK_PREFIX = "T#smoke-"
COUNT_NAMES = ("control", "memberships", "observations", "s3_versions")


def scan_keys(table, filter_expression):
    """Maintenance only: the app itself never scans ObservationsTable."""
    kwargs = {"FilterExpression": filter_expression}
    while True:
        page = table.scan(**kwargs)
        yield from page.get("Items", [])
        if "LastEvaluatedKey" not in page:
            return
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def tenant_from_pk(value):
    """Return the smoke tenant in T#<id> or T#<id>#P#<product>, or None."""
    parts = value.split("#")
    if parts[0] == "T" and len(parts) >= 2 and SMOKE_TENANT.fullmatch(parts[1]):
        return parts[1]
    return None


def key_of(item):
    return {"pk": item["pk"], "sk": item["sk"]}


def s3_versions(s3, bucket, tenant_id):
    # The trailing slash stops a longer tenant id from matching this prefix.
    paginator = s3.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"tenants/{tenant_id}/"):
        for entry in page.get("Versions", []) + page.get("DeleteMarkers", []):
            yield {"Key": entry["Key"], "VersionId": entry["VersionId"]}


def build_plan(control, observations, s3, buckets):
    plan = defaultdict(lambda: {"control": [], "memberships": [], "observations": [], "s3_versions": []})

    for item in scan_keys(control, Attr("pk").begins_with(TENANT_PK_PREFIX)):
        tenant = tenant_from_pk(item["pk"])
        if tenant:
            plan[tenant]["control"].append(key_of(item))

    for item in scan_keys(control, Attr("sk").eq("MEMBERSHIP") & Attr("tenant_id").begins_with("smoke-")):
        if SMOKE_TENANT.fullmatch(item["tenant_id"]):
            plan[item["tenant_id"]]["memberships"].append(key_of(item))

    for item in scan_keys(observations, Attr("pk").begins_with(TENANT_PK_PREFIX)):
        tenant = tenant_from_pk(item["pk"])
        if tenant:
            plan[tenant]["observations"].append(key_of(item))

    for tenant in list(plan):
        for bucket in buckets:
            plan[tenant]["s3_versions"].extend((bucket, v) for v in s3_versions(s3, bucket, tenant))

    return dict(plan)


def delete_plan(plan, control, observations, s3):
    for entries in plan.values():
        with control.batch_writer() as batch:
            for key in entries["control"] + entries["memberships"]:
                batch.delete_item(Key=key)
        with observations.batch_writer() as batch:
            for key in entries["observations"]:
                batch.delete_item(Key=key)
        for bucket, version in entries["s3_versions"]:
            s3.delete_object(Bucket=bucket, **version)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stack", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--yes", action="store_true", help="Delete for real; the default only prints counts")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    stack = session.client("cloudformation").describe_stacks(StackName=args.stack)["Stacks"][0]
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stack.get("Outputs", [])}
    dynamodb = session.resource("dynamodb")
    control = dynamodb.Table(outputs["ControlTable"])
    observations = dynamodb.Table(outputs["ObservationsTable"])
    s3 = session.client("s3")

    plan = build_plan(control, observations, s3, [outputs["RawBucket"], outputs["ReportsBucket"]])

    print("DELETING" if args.yes else "DRY RUN: nothing will be deleted")
    totals = dict.fromkeys(COUNT_NAMES, 0)
    for tenant in sorted(plan):
        counts = {name: len(plan[tenant][name]) for name in COUNT_NAMES}
        for name, count in counts.items():
            totals[name] += count
        print(tenant, " ".join(f"{name}={count}" for name, count in counts.items()))
    print(f"TOTAL tenants={len(plan)}", " ".join(f"{name}={count}" for name, count in totals.items()))

    if args.yes:
        delete_plan(plan, control, observations, s3)
        print("Done.")


if __name__ == "__main__":
    main()
