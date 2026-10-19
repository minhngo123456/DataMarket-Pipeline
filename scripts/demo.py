"""Run real worker functions against Moto's in-process AWS emulator."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import boto3
from moto import mock_aws

from marketdata.queries import history
from marketdata.repository import DynamoStore
from services.ingestion.csv_worker import process_event
from services.normalizer.app import process_batch


def demo():
    # Fake credentials only. This command must not use the caller's AWS account.
    os.environ.update(
        AWS_ACCESS_KEY_ID="testing",
        AWS_SECRET_ACCESS_KEY="testing",
        AWS_DEFAULT_REGION="us-east-1",
        AWS_EC2_METADATA_DISABLED="true",
    )
    now = datetime(2026, 10, 19, 1, 0, tzinfo=UTC)
    with mock_aws():
        db = boto3.client("dynamodb")
        for name in ("control", "observations"):
            db.create_table(
                TableName=name,
                BillingMode="PAY_PER_REQUEST",
                AttributeDefinitions=[
                    {"AttributeName": k, "AttributeType": "S"} for k in ("pk", "sk")
                ],
                KeySchema=[
                    {"AttributeName": "pk", "KeyType": "HASH"},
                    {"AttributeName": "sk", "KeyType": "RANGE"},
                ],
            )
        store = DynamoStore("control", "observations")
        for tenant in ("tenant-a", "tenant-b"):
            store.put(tenant, "META", {"status": "active", "product_count": 0})
            store.put(tenant, "SOURCE#vendor", {"kind": "csv", "enabled": True})
            store.create_product(tenant, "phone", "Phone")
            store.set_mapping(tenant, "vendor", "001-PHONE", "phone")
        key = "tenants/tenant-a/raw/demo-job"
        store.create_job(
            "tenant-a",
            "demo-job",
            {"source_id": "vendor", "kind": "csv", "status": "queued", "raw_key": key},
        )
        s3, sqs = boto3.client("s3"), boto3.client("sqs")
        bucket = "demo-marketdata-raw"
        s3.create_bucket(Bucket=bucket)
        s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
        fixture = Path(__file__).resolve().parents[1] / "tests/fixtures/prices.csv"
        version = s3.put_object(Bucket=bucket, Key=key, Body=fixture.read_bytes())["VersionId"]
        queue = sqs.create_queue(QueueName="transform")["QueueUrl"]
        event = {
            "source": "aws.s3",
            "detail-type": "Object Created",
            "detail": {"bucket": {"name": bucket}, "object": {"key": key, "version-id": version}},
        }
        for _ in range(2):
            process_event(event, store, s3, sqs, bucket, queue)
        failures = []
        while True:
            messages = sqs.receive_message(QueueUrl=queue, MaxNumberOfMessages=10).get(
                "Messages", []
            )
            if not messages:
                break
            batch = {
                "Records": [{"messageId": m["MessageId"], "body": m["Body"]} for m in messages]
            }
            failures.extend(process_batch(batch, store, now)["batchItemFailures"])
            for message in messages:
                sqs.delete_message(QueueUrl=queue, ReceiptHandle=message["ReceiptHandle"])
        counts = store.refresh_job("tenant-a", "demo-job")
        observations = len(history(store, "tenant-a", "phone", now))
        other_tenant = len(history(store, "tenant-b", "phone", now))
        assert counts == {"accepted": 90, "pending": 0, "rejected": 10}
        assert observations == 90 and other_tenant == 0 and not failures
        print(
            json.dumps(
                {
                    "counts_after_processing_file_twice": counts,
                    "tenant_a_observations": observations,
                    "tenant_b_observations": other_tenant,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    demo()
