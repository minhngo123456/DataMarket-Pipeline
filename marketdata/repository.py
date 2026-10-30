"""DynamoDB access. Every public read/write requires a tenant identifier."""

import os
from datetime import UTC, datetime, timedelta

import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

from marketdata.contracts import (
    AccessDenied,
    Conflict,
    QuotaExceeded,
    identifier,
    iso,
    stable_id,
    timestamp,
)
from marketdata.normalize import sku


def pk(tenant_id):
    return "T#" + identifier(tenant_id)


def serialized(item):
    codec = TypeSerializer()
    return {k: codec.serialize(v) for k, v in item.items()}


class DynamoStore:
    def __init__(self, control_table=None, observations_table=None, resource=None, client=None):
        resource = resource or boto3.resource("dynamodb")
        self.control_name = control_table or os.environ["CONTROL_TABLE"]
        self.observations_name = observations_table or os.environ["OBSERVATIONS_TABLE"]
        self.control = resource.Table(self.control_name)
        self.observations = resource.Table(self.observations_name)
        self.client = client or boto3.client("dynamodb")

    def get(self, tenant_id, key):
        return self.control.get_item(Key={"pk": pk(tenant_id), "sk": key}, ConsistentRead=True).get(
            "Item"
        )

    def put(self, tenant_id, key, item):
        # Internal configuration/seed operation, never expose this directly as an API.
        self.control.put_item(Item={**item, "pk": pk(tenant_id), "sk": key})

    @staticmethod
    def pages(table, **kwargs):
        while True:
            response = table.query(**kwargs)
            yield from response.get("Items", [])
            if "LastEvaluatedKey" not in response:
                break
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    def tenant(self, tenant_id):
        result = self.get(tenant_id, "META")
        if not result or result.get("status") != "active":
            raise AccessDenied("tenant_unavailable")
        return result

    def source(self, tenant_id, source_id):
        self.tenant(tenant_id)
        result = self.get(tenant_id, "SOURCE#" + identifier(source_id))
        if not result or not result.get("enabled"):
            raise AccessDenied("source_unavailable")
        return result

    def require_job(self, value):
        tenant = self.tenant(value["tenant_id"])
        source = self.source(value["tenant_id"], value["source_id"])
        job = self.get(value["tenant_id"], "JOB#" + identifier(value["job_id"]))
        if (
            not job
            or job.get("source_id") != value["source_id"]
            or job.get("status") not in ("queued", "extracting", "normalizing", "completed")
            or job.get("kind") != source.get("kind")
        ):
            raise AccessDenied("job_unavailable")
        if "row_number" in value and value["row_number"] > job.get("row_count", 0):
            raise AccessDenied("row_not_in_job")
        return tenant, source, job

    def product(self, tenant_id, product_id):
        self.tenant(tenant_id)
        result = self.get(tenant_id, "PRODUCT#" + identifier(product_id))
        if not result:
            raise AccessDenied("product_unavailable")
        return result

    def create_product(self, tenant_id, product_id, name, attributes=None):
        tenant = self.tenant(tenant_id)
        product_id = identifier(product_id)
        quota = min(int(tenant.get("max_skus", 100)), 100)
        if quota < 1:
            raise QuotaExceeded("catalog_limit_reached")
        try:
            self.client.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": self.control_name,
                            "Key": serialized({"pk": pk(tenant_id), "sk": "META"}),
                            "UpdateExpression": "ADD product_count :one",
                            "ConditionExpression": "#status = :active AND (attribute_not_exists(product_count) OR product_count < :limit)",
                            "ExpressionAttributeNames": {"#status": "status"},
                            "ExpressionAttributeValues": serialized(
                                {":one": 1, ":limit": quota, ":active": "active"}
                            ),
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.control_name,
                            "Item": serialized(
                                {
                                    "pk": pk(tenant_id),
                                    "sk": "PRODUCT#" + product_id,
                                    "product_id": product_id,
                                    "name": name,
                                    "attributes": attributes or {},
                                }
                            ),
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                ]
            )
        except self.client.exceptions.TransactionCanceledException as exc:
            if self.get(tenant_id, "PRODUCT#" + product_id):
                raise Conflict("product_already_exists") from exc
            if int(self.tenant(tenant_id).get("product_count", 0)) >= quota:
                raise QuotaExceeded("catalog_limit_reached") from exc
            raise

    def mapping(self, tenant_id, source_id, source_sku):
        return self.get(
            tenant_id, "MAP#" + identifier(source_id) + "#" + stable_id(sku(source_sku))
        )

    def set_mapping(self, tenant_id, source_id, source_sku, product_id):
        # API owner must check Analyst/Admin role before calling this operation.
        self.source(tenant_id, source_id)
        self.product(tenant_id, product_id)
        self.put(
            tenant_id,
            "MAP#" + source_id + "#" + stable_id(sku(source_sku)),
            {"source_sku": sku(source_sku), "product_id": product_id},
        )

    def receipt(self, tenant_id, observation_id):
        return self.get(tenant_id, "RECEIPT#" + identifier(observation_id))

    def save_receipt(self, receipt):
        try:
            self.control.put_item(
                Item={
                    **receipt,
                    "pk": pk(receipt["tenant_id"]),
                    "sk": "RECEIPT#" + receipt["observation_id"],
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
            return True
        except self.control.meta.client.exceptions.ConditionalCheckFailedException:
            return False

    @staticmethod
    def observation_item(receipt):
        observed = timestamp(receipt["observed_at"])
        return {
            **{k: v for k, v in receipt.items() if k not in ("pk", "sk", "reason")},
            "pk": f"{pk(receipt['tenant_id'])}#P#{identifier(receipt['product_id'])}",
            "sk": f"{receipt['source_id']}#{receipt['observed_at']}#{receipt['observation_id']}",
            "gsi1pk": f"{pk(receipt['tenant_id'])}#DAY#{observed:%Y%m%d}",
            "gsi1sk": f"{receipt['observed_at']}#{receipt['product_id']}#{receipt['source_id']}#{receipt['observation_id']}",
            "match_status": "confirmed",
        }

    def save_observation(self, receipt):
        # The receipt is tenant-wide: changing a mapping cannot duplicate the same row under a new product.
        try:
            self.client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self.control_name,
                            "Item": serialized(
                                {
                                    **receipt,
                                    "pk": pk(receipt["tenant_id"]),
                                    "sk": "RECEIPT#" + receipt["observation_id"],
                                }
                            ),
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.observations_name,
                            "Item": serialized(self.observation_item(receipt)),
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                ]
            )
            return True
        except self.client.exceptions.TransactionCanceledException:
            if self.receipt(receipt["tenant_id"], receipt["observation_id"]):
                return False
            raise

    def approve_pending(self, tenant_id, observation_id, product_id, now=None):
        self.product(tenant_id, product_id)
        pending = self.receipt(tenant_id, observation_id)
        if not pending:
            raise AccessDenied("pending_row_unavailable")
        if int(pending["expires_at"]) <= (now or datetime.now(UTC)).timestamp():
            raise Conflict("pending_row_expired")
        self.source(tenant_id, pending["source_id"])
        if pending["status"] == "accepted" and pending["product_id"] == product_id:
            return
        if pending["status"] != "pending":
            raise Conflict("row_is_not_pending")
        accepted = {**pending, "status": "accepted", "product_id": product_id}
        accepted.pop("reason", None)
        try:
            self.client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self.control_name,
                            "Item": serialized(accepted),
                            "ConditionExpression": "#status = :pending",
                            "ExpressionAttributeNames": {"#status": "status"},
                            "ExpressionAttributeValues": serialized({":pending": "pending"}),
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.observations_name,
                            "Item": serialized(self.observation_item(accepted)),
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.control_name,
                            "Item": serialized(
                                {
                                    "pk": pk(tenant_id),
                                    "sk": "MAP#"
                                    + pending["source_id"]
                                    + "#"
                                    + stable_id(pending["source_sku"]),
                                    "source_sku": pending["source_sku"],
                                    "product_id": product_id,
                                }
                            ),
                        }
                    },
                ]
            )
        except self.client.exceptions.TransactionCanceledException as exc:
            raise Conflict("pending_row_changed") from exc

    def reject_pending(self, tenant_id, observation_id, now=None):
        pending = self.receipt(tenant_id, observation_id)
        if not pending:
            raise AccessDenied("pending_row_unavailable")
        if pending["status"] == "rejected":
            return
        if pending["status"] != "pending":
            raise Conflict("row_is_not_pending")
        try:
            self.control.update_item(
                Key={"pk": pk(tenant_id), "sk": "RECEIPT#" + identifier(observation_id)},
                UpdateExpression="SET #status = :rejected, reviewed_at = :now",
                ConditionExpression="#status = :pending",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":rejected": "rejected",
                    ":pending": "pending",
                    ":now": iso(now or datetime.now(UTC)),
                },
            )
        except self.control.meta.client.exceptions.ConditionalCheckFailedException as exc:
            raise Conflict("pending_row_changed") from exc

    def receipts(self, tenant_id, job_id=None, status=None):
        self.tenant(tenant_id)
        result = self.pages(
            self.control,
            KeyConditionExpression=Key("pk").eq(pk(tenant_id)) & Key("sk").begins_with("RECEIPT#"),
            ConsistentRead=True,
        )
        return [
            r
            for r in result
            if (job_id is None or r["job_id"] == job_id)
            and (status is None or r["status"] == status)
        ]

    def product_history(self, tenant_id, product_id):
        self.product(tenant_id, product_id)
        return self.pages(
            self.observations,
            KeyConditionExpression=Key("pk").eq(f"{pk(tenant_id)}#P#{identifier(product_id)}"),
            ConsistentRead=True,
        )

    @staticmethod
    def observed(row):
        return timestamp(row["observed_at"])

    def window(self, tenant_id, start, end):
        self.tenant(tenant_id)
        day = start.date()
        while day <= end.date():
            rows = self.pages(
                self.observations,
                IndexName="by_day",
                KeyConditionExpression=Key("gsi1pk").eq(f"{pk(tenant_id)}#DAY#{day:%Y%m%d}")
                & Key("gsi1sk").between(iso(start), iso(end)),
            )
            for row in rows:
                if start <= self.observed(row) < end:
                    yield row
            day += timedelta(days=1)

    def update_job(self, tenant_id, job_id, **fields):
        names = {f"#f{i}": name for i, name in enumerate(fields)}
        values = {f":v{i}": value for i, value in enumerate(fields.values())}
        self.control.update_item(
            Key={"pk": pk(tenant_id), "sk": "JOB#" + identifier(job_id)},
            UpdateExpression="SET " + ", ".join(f"#f{i} = :v{i}" for i in range(len(fields))),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ConditionExpression="attribute_exists(pk)",
        )

    def bind_csv_version(self, tenant_id, job_id, version):
        try:
            self.control.update_item(
                Key={"pk": pk(tenant_id), "sk": "JOB#" + identifier(job_id)},
                UpdateExpression="SET raw_version = :version",
                ConditionExpression="attribute_exists(pk) AND (attribute_not_exists(raw_version) OR raw_version = :version)",
                ExpressionAttributeValues={":version": version},
            )
        except self.control.meta.client.exceptions.ConditionalCheckFailedException as exc:
            raise AccessDenied("upload_version_changed") from exc

    def refresh_job(self, tenant_id, job_id):
        # Count durable receipts rather than incrementing a counter on retries.
        rows = self.receipts(tenant_id, job_id=job_id)
        job = self.get(tenant_id, "JOB#" + identifier(job_id))
        counts = {
            s: sum(r["status"] == s for r in rows) for s in ("accepted", "pending", "rejected")
        }
        status = "completed" if len(rows) == job.get("row_count") else "normalizing"
        self.update_job(tenant_id, job_id, status=status, counts=counts)
        return counts

    def active_tenants(self):
        # Small control table only; never scan the observations table. Paginate every scan.
        kwargs = {
            "FilterExpression": "sk = :meta AND #status = :active",
            "ExpressionAttributeNames": {"#status": "status"},
            "ExpressionAttributeValues": {":meta": "META", ":active": "active"},
        }
        while True:
            page = self.control.scan(**kwargs)
            yield from page.get("Items", [])
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def sources(self, tenant_id):
        return self.pages(
            self.control,
            KeyConditionExpression=Key("pk").eq(pk(tenant_id)) & Key("sk").begins_with("SOURCE#"),
            ConsistentRead=True,
        )

    def create_job(self, tenant_id, job_id, fields):
        try:
            self.control.put_item(
                Item={
                    **fields,
                    "job_id": identifier(job_id),
                    "tenant_id": tenant_id,
                    "pk": pk(tenant_id),
                    "sk": "JOB#" + job_id,
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    def acquire_report_lock(self, tenant_id, date_str, job_id=None, triggered_by=None):
        """Acquire a daily report lock atomically using DynamoDB Conditional Write.

        Prevents duplicate AI report generation by concurrent requests or schedulers.
        Uses composite key pk=T#<tenant>, sk=LOCK#YYYYMMDD.
        Returns True if lock was acquired, False if lock was already held.
        """
        tenant_id = identifier(tenant_id)
        date_clean = date_str.replace("-", "")
        now = datetime.now(UTC)
        item = {
            "pk": pk(tenant_id),
            "sk": "LOCK#" + date_clean,
            "tenant_id": tenant_id,
            "date": date_str,
            "job_id": identifier(job_id) if job_id else None,
            "triggered_by": triggered_by,
            "status": "locked",
            "created_at": iso(now),
            "expires_at": int((now + timedelta(days=2)).timestamp()),
        }
        try:
            self.control.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(pk)",
            )
            # Also record REPORT_DAILY# for backward compatibility
            try:
                self.control.put_item(
                    Item={**item, "sk": f"REPORT_DAILY#{date_str}"},
                    ConditionExpression="attribute_not_exists(pk)",
                )
            except ClientError:
                pass
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def get_report_lock(self, tenant_id, date_str):
        """Check if a report lock exists for a tenant on a specific date."""
        date_clean = date_str.replace("-", "")
        return self.get(tenant_id, "LOCK#" + date_clean) or self.get(tenant_id, f"REPORT_DAILY#{date_str}")
