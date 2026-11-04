"""Run the API and HTML source workers against Moto without using an AWS account."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import boto3
from moto import mock_aws

from marketdata.queries import history
from marketdata.repository import DynamoStore
from services.ingestion.api_worker import process_event as process_api_event
from services.ingestion.web_worker import process_event as process_web_event
from services.normalizer.app import process_batch as process_normalizer_batch

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 19, 1, 0, tzinfo=UTC)


def create_tables():
    client = boto3.client("dynamodb")
    client.create_table(
        TableName="control",
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"} for key in ("pk", "sk")],
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
    )
    client.create_table(
        TableName="observations",
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": key, "AttributeType": "S"} for key in ("pk", "sk", "gsi1pk", "gsi1sk")
        ],
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "by_day",
                "Projection": {"ProjectionType": "ALL"},
                "KeySchema": [
                    {"AttributeName": "gsi1pk", "KeyType": "HASH"},
                    {"AttributeName": "gsi1sk", "KeyType": "RANGE"},
                ],
            }
        ],
    )


def demo():
    os.environ.update(
        AWS_ACCESS_KEY_ID="testing",
        AWS_SECRET_ACCESS_KEY="testing",
        AWS_DEFAULT_REGION="us-east-1",
        AWS_EC2_METADATA_DISABLED="true",
        SOURCE_SNAPSHOT_BUCKET="source-snapshots-demo",
    )
    with mock_aws():
        create_tables()
        s3 = boto3.client("s3")
        snapshot_bucket = os.environ["SOURCE_SNAPSHOT_BUCKET"]
        s3.create_bucket(Bucket=snapshot_bucket)
        s3.put_bucket_versioning(
            Bucket=snapshot_bucket, VersioningConfiguration={"Status": "Enabled"}
        )
        store = DynamoStore("control", "observations")
        store.put(
            "tenant-a",
            "META",
            {"status": "active", "product_count": 0, "max_skus": 100, "max_sources": 3},
        )
        store.put(
            "tenant-b",
            "META",
            {"status": "active", "product_count": 0, "max_skus": 100, "max_sources": 3},
        )

        for product_id, name in (
            ("phone", "Phone"),
            ("laptop", "Laptop"),
            ("tablet", "Tablet"),
        ):
            store.create_product("tenant-a", product_id, name)
        store.create_product("tenant-b", "phone", "Phone - tenant B")

        sources = (
            ("api_supplier", "api", {"endpoint": "https://api.example/prices"}),
            ("web_competitor", "web", {"url": "https://web.example/prices"}),
        )
        for source_id, kind, location in sources:
            store.put(
                "tenant-a",
                "SOURCE#" + source_id,
                {"source_id": source_id, "kind": kind, "enabled": True, **location},
            )
            store.create_job(
                "tenant-a",
                source_id + "-demo-job",
                {"source_id": source_id, "kind": kind, "status": "queued", "row_count": 0},
            )
            for source_sku, product_id in (
                ("001-PHONE", "phone"),
                ("002-LAPTOP", "laptop"),
                ("003-TABLET", "tablet"),
            ):
                store.set_mapping("tenant-a", source_id, source_sku, product_id)

        sqs = boto3.client("sqs")
        transform_queue = sqs.create_queue(QueueName="demo-transform")["QueueUrl"]
        api_event = {
            "schema_version": 1,
            "tenant_id": "tenant-a",
            "source_id": "api_supplier",
            "job_id": "api_supplier-demo-job",
            "kind": "api",
        }
        web_event = {
            "schema_version": 1,
            "tenant_id": "tenant-a",
            "source_id": "web_competitor",
            "job_id": "web_competitor-demo-job",
            "kind": "web",
        }
        api_fixture = (ROOT / "tests/fixtures/api_response.json").read_bytes()
        web_fixture = (ROOT / "tests/fixtures/sample_page.html").read_bytes()
        for _ in range(2):
            process_api_event(
                api_event,
                store,
                sqs,
                transform_queue,
                fetcher=lambda *_args, **_kwargs: api_fixture,
            )
            process_web_event(
                web_event,
                store,
                sqs,
                transform_queue,
                fetcher=lambda *_args, **_kwargs: web_fixture,
            )

        failures = []
        while True:
            messages = sqs.receive_message(QueueUrl=transform_queue, MaxNumberOfMessages=10).get(
                "Messages", []
            )
            if not messages:
                break
            batch = {
                "Records": [
                    {"messageId": message["MessageId"], "body": message["Body"]}
                    for message in messages
                ]
            }
            failures.extend(process_normalizer_batch(batch, store, NOW)["batchItemFailures"])
            for message in messages:
                sqs.delete_message(QueueUrl=transform_queue, ReceiptHandle=message["ReceiptHandle"])

        counts = {
            job_id: store.refresh_job("tenant-a", job_id)
            for job_id in ("api_supplier-demo-job", "web_competitor-demo-job")
        }
        tenant_a_observations = sum(
            len(history(store, "tenant-a", product_id, NOW))
            for product_id in ("phone", "laptop", "tablet")
        )
        tenant_b_observations = len(history(store, "tenant-b", "phone", NOW))
        summary = {
            "job_counts": counts,
            "tenant_a_observations_after_workers_ran_twice": tenant_a_observations,
            "tenant_b_observations": tenant_b_observations,
            "normalizer_failures": failures,
        }
        assert all(
            result == {"accepted": 3, "pending": 0, "rejected": 0} for result in counts.values()
        )
        assert tenant_a_observations == 6
        assert tenant_b_observations == 0
        assert not failures
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    demo()
