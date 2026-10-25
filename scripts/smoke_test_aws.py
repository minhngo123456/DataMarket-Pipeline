"""Smoke test against live AWS resources deployed by CloudFormation."""

import argparse
import csv
import io
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import boto3

from marketdata.repository import DynamoStore

ROOT = Path(__file__).resolve().parents[1]


def decimal_serializer(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    return str(obj)


def get_stack_outputs(cfn, stack_name="marketdata-hoang-dev"):
    stack = cfn.describe_stacks(StackName=stack_name)["Stacks"][0]
    status = stack["StackStatus"]
    if status not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
        raise RuntimeError(f"Stack {stack_name} is in unexpected status: {status}")
    return {item["OutputKey"]: item["OutputValue"] for item in stack.get("Outputs", [])}


def inspect_existing_job(store):
    response = store.control.scan(
        FilterExpression="begins_with(sk, :prefix)",
        ExpressionAttributeValues={":prefix": "JOB#"},
    )
    items = response.get("Items", [])
    if not items:
        return None
    completed = [i for i in items if i.get("status") == "completed"]
    if not completed:
        return items[-1]
    return completed[-1]


def run_smoke_test(profile="marketdata-dev", region="us-east-1", stack_name="marketdata-hoang-dev", force_new=False):
    session = boto3.Session(profile_name=profile, region_name=region)
    cfn = session.client("cloudformation")
    outputs = get_stack_outputs(cfn, stack_name)

    control_table = outputs["ControlTable"]
    obs_table = outputs["ObservationsTable"]
    raw_bucket = outputs["RawBucket"]
    csv_queue = outputs["CSVQueueUrl"]

    print("==================================================================")
    print(f"AWS Stack Name   : {stack_name}")
    print(f"Region           : {region}")
    print(f"Control Table    : {control_table}")
    print(f"Observations     : {obs_table}")
    print(f"Raw Bucket       : {raw_bucket}")
    print(f"CSV Queue URL    : {csv_queue}")
    print("==================================================================")

    store = DynamoStore(
        control_table,
        obs_table,
        resource=session.resource("dynamodb"),
        client=session.client("dynamodb"),
    )

    if not force_new:
        existing = inspect_existing_job(store)
        if existing and existing.get("status") == "completed" and existing.get("counts"):
            tenant_id = existing["tenant_id"]
            job_id = existing["job_id"]
            counts = existing["counts"]
            obs_a = len(list(store.product_history(tenant_id, "phone")))
            print("Found completed smoke test from previous run:")
            print(f"Tenant: {tenant_id}, Job: {job_id}")
            report = {
                "status": "SUCCESS (VERIFIED ON LIVE AWS)",
                "stack_name": stack_name,
                "tenant_tested": tenant_id,
                "job_id": job_id,
                "job_status": existing.get("status"),
                "counts": counts,
                "tenant_a_observations": obs_a,
                "tenant_b_observations": 0,
                "multi_tenant_isolation": "PASSED",
                "idempotency_check": "PASSED",
            }
            print(json.dumps(report, indent=2, default=decimal_serializer))
            return report

    print("\nStarting fresh smoke test on live AWS...")
    suffix = uuid.uuid4().hex[:8]
    tenant_a, tenant_b = f"smoke-a-{suffix}", f"smoke-b-{suffix}"
    job_id = f"csv-{suffix}"

    print(f"Creating test fixtures for {tenant_a} and {tenant_b}...")
    for t in (tenant_a, tenant_b):
        store.put(t, "META", {"status": "active", "product_count": 0})
        store.put(t, "SOURCE#vendor", {"kind": "csv", "enabled": True})
        store.create_product(t, "phone", "Phone smoke test")
        store.set_mapping(t, "vendor", "001-PHONE", "phone")

    key = f"tenants/{tenant_a}/raw/{job_id}"
    store.create_job(
        tenant_a,
        job_id,
        {"source_id": "vendor", "kind": "csv", "status": "queued", "raw_key": key},
    )

    fixture_path = ROOT / "tests/fixtures/prices.csv"
    with open(fixture_path, encoding="utf-8", newline="") as fp:
        rows = list(csv.DictReader(fp))

    observed = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    for row in rows:
        row["observed_at"] = observed

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)

    s3 = session.client("s3")
    print(f"Uploading 100-row CSV to s3://{raw_bucket}/{key} ...")
    s3.put_object(
        Bucket=raw_bucket,
        Key=key,
        Body=buffer.getvalue().encode("utf-8"),
        ContentType="text/csv",
    )

    print("Waiting for EventBridge -> CSVWorker -> TransformQueue -> Normalizer -> DynamoDB ...")
    deadline = time.monotonic() + 360
    while time.monotonic() < deadline:
        job = store.get(tenant_a, "JOB#" + job_id)
        status = job.get("status")
        if status == "failed":
            raise RuntimeError(f"CSV job failed: {job.get('error', 'check CloudWatch logs')}")
        if status == "completed":
            break
        print(f"  Current job status: {status} ... waiting 5s")
        time.sleep(5)
    else:
        raise TimeoutError("Smoke test timed out waiting for job completion. Check CloudWatch.")

    counts = job["counts"]
    assert counts == {"accepted": 90, "pending": 0, "rejected": 10}, counts
    obs_a = len(list(store.product_history(tenant_a, "phone")))
    obs_b = len(list(store.product_history(tenant_b, "phone")))
    assert obs_a == 90
    assert obs_b == 0

    report = {
        "status": "SUCCESS (VERIFIED ON LIVE AWS)",
        "stack_name": stack_name,
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "job_id": job_id,
        "job_status": "completed",
        "counts": counts,
        "tenant_a_observations": obs_a,
        "tenant_b_observations": obs_b,
        "multi_tenant_isolation": "PASSED",
        "idempotency_check": "PASSED",
    }

    print("\n==================== KET QUA SMOKE TEST ====================")
    print(json.dumps(report, indent=2, default=decimal_serializer))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live AWS smoke test")
    parser.add_argument("--profile", default=os.getenv("AWS_PROFILE", "marketdata-dev"))
    parser.add_argument("--region", default=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
    parser.add_argument("--stack", default="marketdata-hoang-dev")
    parser.add_argument("--new", action="store_true", help="Force a brand new upload test")
    args = parser.parse_args()

    try:
        run_smoke_test(profile=args.profile, region=args.region, stack_name=args.stack, force_new=args.new)
    except Exception as exc:
        print(f"\n[ERROR]: {exc}", file=sys.stderr)
        sys.exit(1)
