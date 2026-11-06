"""Move one tenant's observations to content-based ids and remove duplicates.

Run after the normalizer that writes content-based ids is deployed. Prints what would
change unless --yes is passed. New rows are written before old rows are deleted, so an
interrupted run can simply be repeated. Legacy job-and-row ids collapse into content
ids; current ids retain their source generation, even when its metadata was not stored.
Unknown ids without generation metadata are preserved rather than merged speculatively.

Usage:
    python -m scripts.migrate_observation_ids --stack marketdata-dev --tenant group07 \
        --region ap-southeast-1 [--profile marketdata-dev] [--yes]
"""

import argparse
from collections import defaultdict

import boto3
from boto3.dynamodb.conditions import Attr

from marketdata.contracts import identifier, observation_content_id, stable_id
from marketdata.repository import DynamoStore


def tenant_observations(table, tenant_id):
    """Maintenance only: the app itself never scans ObservationsTable."""
    kwargs = {"FilterExpression": Attr("pk").begins_with(f"T#{identifier(tenant_id)}#P#")}
    while True:
        page = table.scan(**kwargs)
        yield from page.get("Items", [])
        if "LastEvaluatedKey" not in page:
            return
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def migration_content_id(row, tenant_id):
    content_id = observation_content_id(
        tenant_id,
        row["source_id"],
        row["source_sku"],
        row["observed_at"],
        row["price_basis"],
        row["price"],
    )
    if row.get("source_generation") is not None:
        return stable_id(row["source_generation"], content_id)
    if "job_id" in row and "row_number" in row:
        legacy_id = stable_id(tenant_id, row["source_id"], row["job_id"], row["row_number"])
        if row["observation_id"] == legacy_id:
            return content_id
    # Generation-based observations originally omitted generation metadata. Their
    # existing content ids are authoritative; hashing only the visible fields
    # would erase the source lifetime and duplicate the next ingestion.
    return row["observation_id"]


def build_plan(rows, tenant_id):
    groups = defaultdict(list)
    for row in rows:
        content_id = migration_content_id(row, tenant_id)
        groups[(row["pk"], content_id)].append(row)

    plan = {"observations": 0, "unique": len(groups), "duplicate_groups": 0, "writes": [], "deletes": []}
    for (_, content_id), group in groups.items():
        plan["observations"] += len(group)
        plan["duplicate_groups"] += len(group) > 1
        target = DynamoStore.observation_item({**group[0], "content_id": content_id})
        if not any(row["sk"] == target["sk"] for row in group):
            plan["writes"].append(target)
        plan["deletes"] += [{"pk": row["pk"], "sk": row["sk"]} for row in group if row["sk"] != target["sk"]]
    return plan


def apply_plan(plan, table):
    with table.batch_writer() as batch:
        for item in plan["writes"]:
            batch.put_item(Item=item)
    with table.batch_writer() as batch:
        for key in plan["deletes"]:
            batch.delete_item(Key=key)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stack", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--yes", action="store_true", help="Apply; the default only prints counts")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    stack = session.client("cloudformation").describe_stacks(StackName=args.stack)["Stacks"][0]
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stack.get("Outputs", [])}
    table = session.resource("dynamodb").Table(outputs["ObservationsTable"])

    plan = build_plan(tenant_observations(table, args.tenant), args.tenant)
    print("APPLYING" if args.yes else "DRY RUN: nothing will be changed")
    print(
        f"tenant={args.tenant} observations={plan['observations']} unique={plan['unique']} "
        f"duplicate_groups={plan['duplicate_groups']} writes={len(plan['writes'])} deletes={len(plan['deletes'])}"
    )
    if args.yes:
        apply_plan(plan, table)
        print("Done.")


if __name__ == "__main__":
    main()
