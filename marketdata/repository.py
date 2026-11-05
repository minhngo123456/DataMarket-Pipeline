"""DynamoDB access. Every public read/write requires a tenant identifier."""

import os
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

from marketdata.contracts import (
    AccessDenied,
    Conflict,
    InvalidRecord,
    QuotaExceeded,
    SourceGenerationChanged,
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


def generation_condition(generation):
    """Missing generation identifies legacy records, never a new source's generation."""
    if generation is None:
        return "attribute_not_exists(source_generation)", {}
    return "source_generation = :generation", {":generation": identifier(generation)}


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

    def _change_source_catalog(self, tenant_id, source_id, action, *, creating=False):
        """Serialize source creation/deletion with the tenant quota snapshot.

        Count existing sources instead of relying on a counter that legacy seeds
        never maintained. Every API create/delete changes the catalog revision
        in the same transaction, invalidating a concurrent quota read.
        """
        for _attempt in range(8):
            tenant = self.tenant(tenant_id)
            if creating:
                if self.get(tenant_id, "SOURCE#" + source_id):
                    raise Conflict("source_configuration_changed")
                quota = min(int(tenant.get("max_sources", 3)), 3)
                if sum(1 for _ in self.sources(tenant_id)) >= quota:
                    if self.get(tenant_id, "SOURCE#" + source_id):
                        raise Conflict("source_configuration_changed")
                    raise QuotaExceeded("source_limit_reached")
            values = {":active": "active", ":next_revision": uuid4().hex}
            condition = "attribute_exists(pk) AND #status = :active AND "
            if "source_catalog_revision" in tenant:
                condition += "source_catalog_revision = :previous_revision"
                values[":previous_revision"] = tenant["source_catalog_revision"]
            else:
                condition += "attribute_not_exists(source_catalog_revision)"
            if "max_sources" in tenant:
                condition += " AND max_sources = :quota"
                values[":quota"] = tenant["max_sources"]
            else:
                condition += " AND attribute_not_exists(max_sources)"
            try:
                self.client.transact_write_items(TransactItems=[
                    {"Update": {
                        "TableName": self.control_name,
                        "Key": serialized({"pk": pk(tenant_id), "sk": "META"}),
                        "UpdateExpression": "SET source_catalog_revision = :next_revision",
                        "ConditionExpression": condition,
                        "ExpressionAttributeNames": {"#status": "status"},
                        "ExpressionAttributeValues": serialized(values),
                    }},
                    action,
                ])
                return
            except self.client.exceptions.TransactionCanceledException as exc:
                reasons = exc.response.get("CancellationReasons", [])
                if len(reasons) > 1 and reasons[1].get("Code") == "ConditionalCheckFailed":
                    raise Conflict("source_configuration_changed") from None
                codes = {reason.get("Code") for reason in reasons}
                if not codes.intersection({"ConditionalCheckFailed", "TransactionConflict"}):
                    raise
                # Another source changed the tenant's catalog/quota. Recount
                # before retrying, while keeping this request's source condition.
        raise Conflict("source_configuration_changed")

    def update_source_configuration(self, tenant_id, source_id, fields, *, previous):
        """Update only API-owned configuration, preserving concurrent worker health.

        The opaque revision serializes configuration edits independently of health.
        A stale edit cannot recreate a deleted source or overwrite a replacement.
        """
        tenant_id, source_id = identifier(tenant_id), identifier(source_id)
        self.tenant(tenant_id)
        allowed = {"source_id", "kind", "name", "enabled", "updated_at", "endpoint", "url", "headers"}
        if not fields or not set(fields) <= allowed:
            raise InvalidRecord("invalid_source_configuration")
        if "source_id" in fields and fields["source_id"] != source_id:
            raise InvalidRecord("invalid_source_configuration")
        if previous is not None and "kind" in fields and fields["kind"] != previous.get("kind"):
            raise Conflict("source_kind_conflict")
        names = {"#revision": "source_config_revision"}
        values = {":revision": uuid4().hex}
        sets = ["#revision = :revision"]
        if previous is None:
            # Only a new source gets a new lifetime identity. Legacy sources stay
            # legacy until deletion so their already queued jobs remain valid.
            sets.append("source_generation = :generation")
            values[":generation"] = uuid4().hex
        removes = []
        for index, (name, value) in enumerate(fields.items()):
            key, parameter = f"#f{index}", f":v{index}"
            names[key] = name
            if value is None:
                removes.append(key)
            else:
                sets.append(f"{key} = {parameter}")
                values[parameter] = value
        condition = "attribute_not_exists(pk)"
        if previous is not None:
            condition = "attribute_exists(pk) AND #kind = :previous_kind AND "
            names["#kind"] = "kind"
            values[":previous_kind"] = previous["kind"]
            if "source_config_revision" in previous:
                condition += "#revision = :previous_revision"
                values[":previous_revision"] = previous["source_config_revision"]
            else:
                # Legacy sources get a revision on their first configuration edit.
                condition += "attribute_not_exists(#revision)"
            generation_guard, generation_values = generation_condition(
                previous.get("source_generation")
            )
            condition += " AND " + generation_guard
            values.update(generation_values)
        update = "SET " + ", ".join(sets)
        if removes:
            update += " REMOVE " + ", ".join(removes)
        if previous is None:
            self._change_source_catalog(tenant_id, source_id, {"Update": {
                "TableName": self.control_name,
                "Key": serialized({"pk": pk(tenant_id), "sk": "SOURCE#" + source_id}),
                "UpdateExpression": update,
                "ConditionExpression": condition,
                "ExpressionAttributeNames": names,
                "ExpressionAttributeValues": serialized(values),
            }}, creating=True)
            return self.get(tenant_id, "SOURCE#" + source_id)
        try:
            return self.control.update_item(
                Key={"pk": pk(tenant_id), "sk": "SOURCE#" + source_id},
                UpdateExpression=update, ConditionExpression=condition,
                ExpressionAttributeNames=names, ExpressionAttributeValues=values,
                ReturnValues="ALL_NEW",
            )["Attributes"]
        except self.control.meta.client.exceptions.ConditionalCheckFailedException:
            raise Conflict("source_configuration_changed") from None

    def delete_source_configuration(self, tenant_id, source_id, *, previous):
        """Delete exactly the configuration read by this request, atomically."""
        tenant_id, source_id = identifier(tenant_id), identifier(source_id)
        self.tenant(tenant_id)
        generation_guard, values = generation_condition(previous.get("source_generation"))
        values[":kind"] = previous["kind"]
        condition = "attribute_exists(pk) AND #kind = :kind AND " + generation_guard
        names = {"#kind": "kind"}
        if "source_config_revision" in previous:
            condition += " AND #revision = :revision"
            names["#revision"] = "source_config_revision"
            values[":revision"] = previous["source_config_revision"]
        else:
            condition += " AND attribute_not_exists(source_config_revision)"
        self._change_source_catalog(tenant_id, source_id, {"Delete": {
            "TableName": self.control_name,
            "Key": serialized({"pk": pk(tenant_id), "sk": "SOURCE#" + source_id}),
            "ConditionExpression": condition,
            "ExpressionAttributeNames": names,
            "ExpressionAttributeValues": serialized(values),
        }})

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

    def members(self, tenant_id):
        self.tenant(tenant_id)
        return list(self.pages(
            self.control,
            KeyConditionExpression=Key("pk").eq(pk(tenant_id)) & Key("sk").begins_with("MEMBER#"),
            ConsistentRead=True,
        ))

    def save_member(self, tenant_id, user_id, role, email, status="active", *, must_exist=False):
        """Write both membership records together without moving a user between tenants.

        The tenant version serializes member changes, including quota checks. Counting
        existing records also supports tenants created before member_count was stored.
        All application membership writes must use this method.
        """
        tenant_id, user_id = identifier(tenant_id), identifier(user_id)
        if (role not in ("admin", "analyst", "viewer") and not (must_exist and role is None)) or (
            status not in ("active", "disabled") and not (must_exist and status is None)
        ):
            raise InvalidRecord("invalid_member")
        for _ in range(3):
            tenant = self.tenant(tenant_id)
            version = tenant.get("membership_version")
            members = self.members(tenant_id)
            previous = next((m for m in members if m["sk"] == "MEMBER#" + user_id), None)
            if must_exist and previous is None:
                raise AccessDenied("member_not_found")
            membership = self.control.get_item(
                Key={"pk": "U#" + user_id, "sk": "MEMBERSHIP"}, ConsistentRead=True
            ).get("Item")
            if membership and membership.get("tenant_id") != tenant_id:
                raise Conflict("member_belongs_to_another_tenant")
            count = len(members) + (previous is None)
            if count > 5:
                raise QuotaExceeded("member_limit_reached")
            record = {
                **(previous or {}), "pk": pk(tenant_id), "sk": "MEMBER#" + user_id,
                "role": role if role is not None else previous["role"],
                "status": status if status is not None else previous["status"],
                "email": email if email is not None else (previous or {}).get("email"),
            }
            values = {":active": "active", ":next": int(version or 0) + 1, ":count": count}
            condition = "#status = :active AND attribute_not_exists(membership_version)"
            if version is not None:
                condition = "#status = :active AND membership_version = :version"
                values[":version"] = version
            try:
                self.client.transact_write_items(TransactItems=[
                    {"Update": {
                        "TableName": self.control_name,
                        "Key": serialized({"pk": pk(tenant_id), "sk": "META"}),
                        "UpdateExpression": "SET membership_version = :next, member_count = :count",
                        "ConditionExpression": condition,
                        "ExpressionAttributeNames": {"#status": "status"},
                        "ExpressionAttributeValues": serialized(values),
                    }},
                    {"Put": {
                        "TableName": self.control_name,
                        "Item": serialized({
                            "pk": "U#" + user_id, "sk": "MEMBERSHIP",
                            "tenant_id": tenant_id, "status": record["status"],
                        }),
                        "ConditionExpression": "attribute_not_exists(pk) OR tenant_id = :tenant",
                        "ExpressionAttributeValues": serialized({":tenant": tenant_id}),
                    }},
                    {"Put": {"TableName": self.control_name, "Item": serialized(record)}},
                ])
                return record
            except self.client.exceptions.TransactionCanceledException as exc:
                reasons = exc.response.get("CancellationReasons", [])
                codes = {r.get("Code") for r in reasons}
                retryable = {"ConditionalCheckFailed", "TransactionConflict"}
                if not codes.intersection(retryable) or not codes.issubset(retryable | {None, "None"}):
                    raise
                # Re-read after a concurrent member update; never apply stale quota or ownership.
        raise Conflict("membership_changed_retry_request")

    def create_tenant(self, tenant_id, user_id, name, email):
        """Create a tenant with this user as its Admin, only if the user has no membership yet.

        The condition on U#<sub> also covers disabled members, so nobody leaves a tenant
        that locked them out by creating another one.
        """
        tenant_id, user_id = identifier(tenant_id), identifier(user_id)
        absent = {"ConditionExpression": "attribute_not_exists(pk)"}
        now = iso(datetime.now(UTC))
        try:
            self.client.transact_write_items(TransactItems=[
                {"Put": {"TableName": self.control_name, **absent, "Item": serialized({
                    "pk": pk(tenant_id), "sk": "META", "status": "active", "name": name,
                    "product_count": 0, "max_skus": 100, "max_sources": 3,
                    "membership_version": 1, "member_count": 1, "created_at": now,
                })}},
                {"Put": {"TableName": self.control_name, **absent, "Item": serialized({
                    "pk": "U#" + user_id, "sk": "MEMBERSHIP", "tenant_id": tenant_id, "status": "active",
                })}},
                {"Put": {"TableName": self.control_name, **absent, "Item": serialized({
                    "pk": pk(tenant_id), "sk": "MEMBER#" + user_id,
                    "role": "admin", "status": "active", "email": email,
                })}},
            ])
        except self.client.exceptions.TransactionCanceledException as exc:
            codes = {r.get("Code") for r in exc.response.get("CancellationReasons", [])}
            if "ConditionalCheckFailed" in codes:
                raise Conflict("user_already_has_membership") from exc
            if "TransactionConflict" in codes:
                raise Conflict("membership_changed_retry_request") from exc
            raise
        return self.get(tenant_id, "META")

    def source(self, tenant_id, source_id):
        self.tenant(tenant_id)
        result = self.get(tenant_id, "SOURCE#" + identifier(source_id))
        if not result or not result.get("enabled"):
            raise AccessDenied("source_unavailable")
        return result

    @staticmethod
    def source_job_matches(source, job):
        return bool(
            source and job
            and source.get("kind") == job.get("kind")
            and source.get("source_generation") == job.get("source_generation")
        )

    def require_job(self, value):
        tenant = self.tenant(value["tenant_id"])
        source = self.get(value["tenant_id"], "SOURCE#" + identifier(value["source_id"]))
        job = self.get(value["tenant_id"], "JOB#" + identifier(value["job_id"]))
        if (
            source and job and job.get("source_id") == value["source_id"]
            and source.get("source_generation") != job.get("source_generation")
        ):
            # An obsolete job stays obsolete even if its replacement is disabled
            # or has another kind. Do not retry it against the replacement.
            raise SourceGenerationChanged("source_generation_changed")
        if not source or not source.get("enabled"):
            raise AccessDenied("source_unavailable")
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
            self.client.transact_write_items(
                TransactItems=[
                    self._source_write_check(receipt),
                    {"Put": {
                        "TableName": self.control_name,
                        "Item": serialized({
                            **receipt,
                            "pk": pk(receipt["tenant_id"]),
                            "sk": "RECEIPT#" + receipt["observation_id"],
                        }),
                        "ConditionExpression": "attribute_not_exists(pk)",
                    }},
                    self._job_receipts_revision_update(receipt),
                ]
            )
            return True
        except self.client.exceptions.TransactionCanceledException as exc:
            return self._receipt_write_cancelled(receipt, exc)

    def _job_receipts_revision_update(self, receipt):
        """Fence count snapshots with the same transaction that changes a receipt."""
        generation_guard, values = generation_condition(receipt.get("source_generation"))
        values.update({":source": identifier(receipt["source_id"]), ":one": 1})
        return {"Update": {
            "TableName": self.control_name,
            "Key": serialized({
                "pk": pk(receipt["tenant_id"]),
                "sk": "JOB#" + identifier(receipt["job_id"]),
            }),
            "UpdateExpression": "ADD receipts_revision :one",
            "ConditionExpression": "attribute_exists(pk) AND source_id = :source AND " + generation_guard,
            "ExpressionAttributeValues": serialized(values),
        }}

    def _source_write_check(self, receipt):
        generation_guard, values = generation_condition(receipt.get("source_generation"))
        values[":enabled"] = True
        return {"ConditionCheck": {
            "TableName": self.control_name,
            "Key": serialized({
                "pk": pk(receipt["tenant_id"]),
                "sk": "SOURCE#" + identifier(receipt["source_id"]),
            }),
            "ConditionExpression": "attribute_exists(pk) AND enabled = :enabled AND " + generation_guard,
            "ExpressionAttributeValues": serialized(values),
        }}

    def _require_receipt_source(self, receipt):
        source = self.get(receipt["tenant_id"], "SOURCE#" + identifier(receipt["source_id"]))
        if not source or source.get("source_generation") != receipt.get("source_generation"):
            raise SourceGenerationChanged("source_generation_changed")
        if not source.get("enabled"):
            raise AccessDenied("source_unavailable")
        return source

    def _receipt_write_cancelled(self, receipt, exc):
        reasons = exc.response.get("CancellationReasons", [])
        if reasons and reasons[0].get("Code") == "ConditionalCheckFailed":
            # The first transaction action always guards source lifetime/enabled.
            self._require_receipt_source(receipt)
            # A source may have been disabled and re-enabled after cancellation.
            # Its generation still matches, so retry rather than dropping a row.
            raise exc
        self._require_receipt_source(receipt)
        if self.receipt(receipt["tenant_id"], receipt["observation_id"]):
            return False
        raise exc

    @staticmethod
    def observation_item(receipt):
        observed = timestamp(receipt["observed_at"])
        # Pending receipts saved before content_id existed fall back to their row id.
        observation_id = receipt.get("content_id") or receipt["observation_id"]
        return {
            **{k: v for k, v in receipt.items() if k not in ("pk", "sk", "reason", "content_id", "source_generation")},
            "observation_id": observation_id,
            "pk": f"{pk(receipt['tenant_id'])}#P#{identifier(receipt['product_id'])}",
            "sk": f"{receipt['source_id']}#{receipt['observed_at']}#{observation_id}",
            "gsi1pk": f"{pk(receipt['tenant_id'])}#DAY#{observed:%Y%m%d}",
            "gsi1sk": f"{receipt['observed_at']}#{receipt['product_id']}#{receipt['source_id']}#{observation_id}",
            "match_status": "confirmed",
        }

    def save_observation(self, receipt):
        # The receipt is tenant-wide: changing a mapping cannot duplicate the same row under a new product.
        try:
            self.client.transact_write_items(
                TransactItems=[
                    self._source_write_check(receipt),
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
                        # No condition: another job may already hold this price point; last write wins.
                        "Put": {
                            "TableName": self.observations_name,
                            "Item": serialized(self.observation_item(receipt)),
                        }
                    },
                    self._job_receipts_revision_update(receipt),
                ]
            )
            return True
        except self.client.exceptions.TransactionCanceledException as exc:
            return self._receipt_write_cancelled(receipt, exc)

    def approve_pending(self, tenant_id, observation_id, product_id, now=None):
        self.product(tenant_id, product_id)
        pending = self.receipt(tenant_id, observation_id)
        if not pending:
            raise AccessDenied("pending_row_unavailable")
        if int(pending["expires_at"]) <= (now or datetime.now(UTC)).timestamp():
            raise Conflict("pending_row_expired")
        self._require_receipt_source(pending)
        if pending["status"] == "accepted" and pending["product_id"] == product_id:
            return
        if pending["status"] != "pending":
            raise Conflict("row_is_not_pending")
        accepted = {**pending, "status": "accepted", "product_id": product_id}
        accepted.pop("reason", None)
        try:
            self.client.transact_write_items(
                TransactItems=[
                    self._source_write_check(pending),
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
                            "ConditionExpression": "attribute_not_exists(pk) OR product_id = :product",
                            "ExpressionAttributeValues": serialized({":product": product_id}),
                        }
                    },
                    self._job_receipts_revision_update(pending),
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
            self.client.transact_write_items(
                TransactItems=[
                    {"Update": {
                        "TableName": self.control_name,
                        "Key": serialized({
                            "pk": pk(tenant_id), "sk": "RECEIPT#" + identifier(observation_id),
                        }),
                        "UpdateExpression": "SET #status = :rejected, reviewed_at = :now",
                        "ConditionExpression": "#status = :pending",
                        "ExpressionAttributeNames": {"#status": "status"},
                        "ExpressionAttributeValues": serialized({
                            ":rejected": "rejected", ":pending": "pending",
                            ":now": iso(now or datetime.now(UTC)),
                        }),
                    }},
                    self._job_receipts_revision_update(pending),
                ]
            )
        except self.client.exceptions.TransactionCanceledException as exc:
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

    def update_job(self, tenant_id, job_id, *, receipt_snapshot=None, **fields):
        names = {f"#f{i}": name for i, name in enumerate(fields)}
        values = {f":v{i}": value for i, value in enumerate(fields.values())}
        condition = "attribute_exists(pk)"
        if receipt_snapshot is not None:
            # Receipt revisions cover inserts and reviews; state and row count
            # also protect against cancellation/failure or extractor updates.
            for index, name in enumerate(("receipts_revision", "status", "row_count")):
                parameter = f"#expected{index}"
                names[parameter] = name
                if name in receipt_snapshot:
                    value = f":expected{index}"
                    values[value] = receipt_snapshot[name]
                    condition += f" AND {parameter} = {value}"
                else:
                    condition += f" AND attribute_not_exists({parameter})"
        self.control.update_item(
            Key={"pk": pk(tenant_id), "sk": "JOB#" + identifier(job_id)},
            UpdateExpression="SET " + ", ".join(f"#f{i} = :v{i}" for i in range(len(fields))),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ConditionExpression=condition,
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

    def begin_csv_normalization(self, tenant_id, job_id, source_id, version, row_count):
        """Keep one bound CSV's row count stable without reopening a completed job."""
        source_id = identifier(source_id)
        if type(row_count) is not int or not 1 <= row_count <= 1000:
            raise InvalidRecord("invalid_csv_row_count")
        try:
            return self.control.update_item(
                Key={"pk": pk(tenant_id), "sk": "JOB#" + identifier(job_id)},
                UpdateExpression="SET row_count = :count, #status = :normalizing",
                ConditionExpression=(
                    "attribute_exists(pk) AND source_id = :source AND kind = :kind "
                    "AND raw_version = :version AND "
                    "(#status IN (:queued, :extracting) OR "
                    "(#status = :normalizing AND row_count = :count))"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":source": source_id, ":kind": "csv", ":version": version,
                    ":count": row_count, ":queued": "queued", ":extracting": "extracting",
                    ":normalizing": "normalizing",
                },
                ReturnValues="ALL_NEW",
            )["Attributes"]
        except self.control.meta.client.exceptions.ConditionalCheckFailedException:
            job = self.get(tenant_id, "JOB#" + identifier(job_id))
            if (
                not job or job.get("source_id") != source_id or job.get("kind") != "csv"
                or job.get("raw_version") != version or job.get("row_count") != row_count
                or job.get("status") != "completed"
            ):
                raise AccessDenied("csv_normalization_binding_conflict") from None
            return job

    def bind_source_snapshot(self, tenant_id, job_id, source_id, kind, snapshot, row_count):
        """Bind immutable rows and their count atomically before any SQS send."""
        try:
            return self.control.update_item(
                Key={"pk": pk(tenant_id), "sk": "JOB#" + identifier(job_id)},
                UpdateExpression="SET source_snapshot = :snapshot, row_count = :count, #status = :normalizing",
                ConditionExpression=(
                    "attribute_exists(pk) AND source_id = :source AND kind = :kind "
                    "AND attribute_not_exists(source_snapshot) "
                    "AND (attribute_not_exists(row_count) OR row_count = :zero) "
                    "AND #status IN (:queued, :extracting, :normalizing)"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":snapshot": snapshot,
                    ":count": row_count,
                    ":zero": 0,
                    ":source": identifier(source_id),
                    ":kind": kind,
                    ":queued": "queued",
                    ":extracting": "extracting",
                    ":normalizing": "normalizing",
                },
                ReturnValues="ALL_NEW",
            )["Attributes"]
        except self.control.meta.client.exceptions.ConditionalCheckFailedException:
            job = self.get(tenant_id, "JOB#" + identifier(job_id))
            if (
                not job
                or job.get("source_id") != source_id
                or job.get("kind") != kind
                or job.get("status") not in ("queued", "extracting", "normalizing", "completed")
                or job.get("source_snapshot") != snapshot
                or job.get("row_count") != row_count
            ):
                raise AccessDenied("source_snapshot_binding_conflict") from None
            # A retry or concurrent delivery must not reset status/counts, in
            # particular if the Normalizer has already completed this job.
            return job

    def fail_source_job(self, tenant_id, job_id, source_id, kind, error, require_unbound=False):
        """Fail only an active matching source job; late duplicates cannot undo completion."""
        condition = (
            "attribute_exists(pk) AND source_id = :source AND kind = :kind "
            "AND attribute_not_exists(collection_succeeded_at) "
            "AND #status IN (:queued, :extracting, :normalizing)"
        )
        if require_unbound:
            condition += " AND attribute_not_exists(source_snapshot)"
        try:
            self.control.update_item(
                Key={"pk": pk(tenant_id), "sk": "JOB#" + identifier(job_id)},
                UpdateExpression="SET #status = :failed, #error = :error",
                ConditionExpression=condition,
                ExpressionAttributeNames={"#status": "status", "#error": "error"},
                ExpressionAttributeValues={
                    ":source": identifier(source_id),
                    ":kind": kind,
                    ":failed": "failed",
                    ":error": error,
                    ":queued": "queued",
                    ":extracting": "extracting",
                    ":normalizing": "normalizing",
                },
            )
            return True
        except self.control.meta.client.exceptions.ConditionalCheckFailedException:
            return False

    def refresh_job(self, tenant_id, job_id):
        # Read the job before its receipts. Every receipt mutation bumps the
        # revision atomically, so an older count snapshot cannot commit later.
        job_id = identifier(job_id)
        for _attempt in range(8):
            job = self.get(tenant_id, "JOB#" + job_id)
            if not job:
                raise AccessDenied("job_unavailable")
            rows = self.receipts(tenant_id, job_id=job_id)
            counts = {
                s: sum(r["status"] == s for r in rows) for s in ("accepted", "pending", "rejected")
            }
            status = job.get("status")
            if status in ("queued", "extracting", "normalizing"):
                status = "completed" if len(rows) == job.get("row_count") else "normalizing"
            try:
                self.update_job(
                    tenant_id, job_id, receipt_snapshot=job, status=status, counts=counts,
                )
                return counts
            except self.control.meta.client.exceptions.ConditionalCheckFailedException:
                continue
        raise Conflict("job_counts_changed")

    def record_source_collection(
        self, tenant_id, source_id, job_id, kind, status, error_code=None, now=None
    ):
        """Record extractor health using trusted job order and conditional writes.

        Success means all extracted rows were enqueued, not that Normalizer
        accepted every row. Old jobs can improve success history but never
        replace the current run, and a successful run cannot regress.
        """
        if kind not in ("csv", "api", "web") or status not in (
            "running", "retrying", "succeeded", "failed"
        ):
            raise InvalidRecord("invalid_collection_status")
        if error_code is not None and (
            not isinstance(error_code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,95}", error_code)
        ):
            raise InvalidRecord("invalid_collection_error_code")
        tenant_id, source_id, job_id = map(identifier, (tenant_id, source_id, job_id))
        self.tenant(tenant_id)
        source = self.get(tenant_id, "SOURCE#" + source_id)
        job = self.get(tenant_id, "JOB#" + job_id)
        allowed_states = ["queued", "extracting", "normalizing"]
        if status == "succeeded":
            allowed_states.append("completed")
        elif status == "failed":
            allowed_states.append("failed")
        if (
            not source or source.get("kind") != kind or not source.get("enabled")
            or not job or job.get("source_id") != source_id or job.get("kind") != kind
            or job.get("status") not in allowed_states
            or not self.source_job_matches(source, job)
        ):
            return False
        at = iso(now or datetime.now(UTC))
        job_at = iso(timestamp(
            job.get("collection_job_at") or job.get("scheduled_at") or job.get("created_at") or at
        ))
        job_values = {":source": source_id, ":kind": kind, ":at": at, ":job_at": job_at}
        job_values.update({f":state{i}": value for i, value in enumerate(allowed_states)})
        state_params = ", ".join(f":state{i}" for i in range(len(allowed_states)))
        job_update = (
            "SET collection_job_at = if_not_exists(collection_job_at, :job_at), "
            "collection_started_at = if_not_exists(collection_started_at, :at)"
        )
        if status == "succeeded":
            job_update += ", collection_succeeded_at = if_not_exists(collection_succeeded_at, :at)"
        try:
            job = self.control.update_item(
                Key={"pk": pk(tenant_id), "sk": "JOB#" + job_id},
                UpdateExpression=job_update,
                ConditionExpression=(
                    "attribute_exists(pk) AND source_id = :source AND kind = :kind "
                    f"AND #status IN ({state_params})"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues=job_values,
                ReturnValues="ALL_NEW",
            )["Attributes"]
        except self.control.meta.client.exceptions.ConditionalCheckFailedException:
            return False
        run_key = job["collection_job_at"] + "#" + job_id
        values = {
            ":kind": kind, ":enabled": True, ":run": run_key,
            ":job_at": job["collection_job_at"], ":job": job_id,
            ":attempt": job["collection_started_at"], ":status": status,
            ":success": "succeeded",
        }
        generation_guard, generation_values = generation_condition(job.get("source_generation"))
        values.update(generation_values)
        same_job_guard = "collection_status <> :success"
        if status in ("running", "retrying"):
            same_job_guard += " AND collection_status <> :failed"
            values[":failed"] = "failed"
        update = (
            "SET collection_job_key = :run, collection_job_at = :job_at, "
            "last_job_id = :job, last_attempt_at = :attempt, collection_status = :status"
        )
        if status in ("retrying", "failed"):
            update += ", last_error_code = :error"
            values[":error"] = error_code or kind + "_worker_error"
        else:
            update += " REMOVE last_error_code"
        changed = False
        try:
            self.control.update_item(
                Key={"pk": pk(tenant_id), "sk": "SOURCE#" + source_id},
                UpdateExpression=update,
                ConditionExpression=(
                    "attribute_exists(pk) AND kind = :kind AND enabled = :enabled AND "
                    f"{generation_guard} AND "
                    "(attribute_not_exists(collection_job_key) OR collection_job_key < :run OR "
                    f"(collection_job_key = :run AND last_job_id = :job AND ({same_job_guard})))"
                ),
                ExpressionAttributeValues=values,
            )
            changed = True
        except self.control.meta.client.exceptions.ConditionalCheckFailedException:
            pass
        if status == "succeeded":
            # Independent monotonic history: a late older success may fill a gap
            # while a newer job is running, without changing its current status.
            try:
                self.control.update_item(
                    Key={"pk": pk(tenant_id), "sk": "SOURCE#" + source_id},
                    UpdateExpression=(
                        "SET last_success_at = :success_at, last_success_job_id = :job, "
                        "last_success_job_at = :job_at, collection_success_key = :run"
                    ),
                    ConditionExpression=(
                        "attribute_exists(pk) AND kind = :kind AND enabled = :enabled "
                        f"AND {generation_guard} "
                        "AND (attribute_not_exists(collection_success_key) OR collection_success_key < :run) "
                        "AND (attribute_not_exists(last_success_at) OR last_success_at <= :success_at)"
                    ),
                    ExpressionAttributeValues={
                        ":kind": kind, ":enabled": True, ":run": run_key,
                        # Timestamp the SOURCE confirmation independently. The
                        # JOB's first success may predate an older job whose
                        # history write won while this write was being retried.
                        ":success_at": at,
                        ":job": job_id, ":job_at": job["collection_job_at"],
                        **generation_values,
                    },
                )
                changed = True
            except self.control.meta.client.exceptions.ConditionalCheckFailedException:
                pass
        return changed

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
        fields = dict(fields)
        if fields.get("kind") in ("csv", "api", "web"):
            if "source_generation" not in fields:
                # Convenience for internal seed/test callers. Production callers
                # pass the generation read with their source, including legacy None.
                source = self.get(tenant_id, "SOURCE#" + identifier(fields["source_id"]))
                fields["source_generation"] = (source or {}).get("source_generation")
            if fields["source_generation"] is None:
                fields.pop("source_generation")
            else:
                identifier(fields["source_generation"])
        try:
            self.control.put_item(
                Item={
                    "created_at": iso(datetime.now(UTC)),
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
