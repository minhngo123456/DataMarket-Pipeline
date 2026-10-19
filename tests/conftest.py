from datetime import UTC, datetime

import boto3
import pytest
from moto import mock_aws

from marketdata.repository import DynamoStore

NOW = datetime(2026, 10, 19, 1, 0, tzinfo=UTC)


def create_tables():
    client = boto3.client("dynamodb")
    for name in ("control", "observations"):
        attrs = [{"AttributeName": k, "AttributeType": "S"} for k in ("pk", "sk")]
        options = {}
        if name == "observations":
            attrs += [{"AttributeName": k, "AttributeType": "S"} for k in ("gsi1pk", "gsi1sk")]
            options["GlobalSecondaryIndexes"] = [
                {
                    "IndexName": "by_day",
                    "Projection": {"ProjectionType": "ALL"},
                    "KeySchema": [
                        {"AttributeName": "gsi1pk", "KeyType": "HASH"},
                        {"AttributeName": "gsi1sk", "KeyType": "RANGE"},
                    ],
                }
            ]
        client.create_table(
            TableName=name,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=attrs,
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            **options,
        )


def seed(store):
    for tenant in ("tenant-a", "tenant-b"):
        store.put(
            tenant,
            "META",
            {"status": "active", "product_count": 0, "max_skus": 100, "max_sources": 3},
        )
        store.put(tenant, "SOURCE#vendor", {"source_id": "vendor", "kind": "csv", "enabled": True})
        store.create_product(
            tenant, "phone", "Phone - " + tenant, {"color": "black", "storage": "128GB"}
        )
        store.set_mapping(tenant, "vendor", "001-PHONE", "phone")
        store.create_job(
            tenant,
            "csv-job",
            {
                "source_id": "vendor",
                "kind": "csv",
                "status": "queued",
                "row_count": 100,
                "raw_key": f"tenants/{tenant}/raw/csv-job",
            },
        )


@pytest.fixture
def store(monkeypatch):
    for name, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_EC2_METADATA_DISABLED": "true",
    }.items():
        monkeypatch.setenv(name, value)
    with mock_aws():
        create_tables()
        result = DynamoStore("control", "observations")
        seed(result)
        yield result


@pytest.fixture
def message():
    return {
        "schema_version": 1,
        "tenant_id": "tenant-a",
        "source_id": "vendor",
        "job_id": "csv-job",
        "row_number": 1,
        "record": {
            "source_sku": "001-PHONE",
            "price": "20100000",
            "currency": "VND",
            "observed_at": "2026-10-19T07:45:00+07:00",
            "price_basis": "tax_included",
        },
    }
