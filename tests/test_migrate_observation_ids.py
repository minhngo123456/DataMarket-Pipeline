from copy import deepcopy

from marketdata.queries import history
from marketdata.service import ingest
from scripts.migrate_observation_ids import apply_plan, build_plan, tenant_observations
from tests.conftest import NOW


def old_row(store, message, job_id, tenant="tenant-a", price="20100000"):
    """Rewrite a stored observation the way the old job-and-row ids looked."""
    value = {**deepcopy(message), "tenant_id": tenant}
    value["record"]["price"] = price
    result = ingest(value, store, NOW)
    item = next(
        row
        for row in tenant_observations(store.observations, tenant)
        if str(row["price"]) == price and not row["observation_id"].startswith("old-")
    )
    store.observations.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
    old_id = f"old-{job_id}"
    store.observations.put_item(
        Item={
            **item,
            "job_id": job_id,
            "observation_id": old_id,
            "sk": f"{item['source_id']}#{item['observed_at']}#{old_id}",
            "gsi1sk": f"{item['observed_at']}#{item['product_id']}#{item['source_id']}#{old_id}",
        }
    )
    store.control.delete_item(Key={"pk": f"T#{tenant}", "sk": "RECEIPT#" + result["observation_id"]})


def rows_of(store, tenant="tenant-a"):
    return list(tenant_observations(store.observations, tenant))


def plan_for(store, tenant="tenant-a"):
    return build_plan(rows_of(store, tenant), tenant)


def test_plan_counts_without_changing_anything(store, message):
    old_row(store, message, "job-1")
    old_row(store, message, "job-2")
    before = sorted(row["sk"] for row in rows_of(store))

    plan = plan_for(store)

    assert (plan["observations"], plan["unique"], plan["duplicate_groups"]) == (2, 1, 1)
    assert (len(plan["writes"]), len(plan["deletes"])) == (1, 2)
    assert sorted(row["sk"] for row in rows_of(store)) == before


def test_apply_collapses_duplicates_and_future_uploads_overwrite(store, message):
    old_row(store, message, "job-1")
    old_row(store, message, "job-2")

    apply_plan(plan_for(store), store.observations)

    rows = history(store, "tenant-a", "phone", NOW)
    assert len(rows) == 1
    assert not rows[0]["observation_id"].startswith("old-")
    assert rows[0]["gsi1sk"].endswith(rows[0]["observation_id"])

    # Same data through the deployed code lands on the migrated row, not a new one.
    ingest(message, store, NOW)
    assert len(history(store, "tenant-a", "phone", NOW)) == 1


def test_rerun_is_a_no_op(store, message):
    old_row(store, message, "job-1")
    apply_plan(plan_for(store), store.observations)

    plan = plan_for(store)

    assert plan["writes"] == [] and plan["deletes"] == []


def test_different_prices_stay_separate(store, message):
    old_row(store, message, "job-1", price="20100000")
    old_row(store, message, "job-2", price="19900000")

    apply_plan(plan_for(store), store.observations)

    assert len(history(store, "tenant-a", "phone", NOW)) == 2


def test_other_tenants_are_untouched(store, message):
    old_row(store, message, "job-1")
    old_row(store, message, "job-b", tenant="tenant-b")
    other = [row["sk"] for row in rows_of(store, "tenant-b")]

    apply_plan(plan_for(store), store.observations)

    assert [row["sk"] for row in rows_of(store, "tenant-b")] == other
    assert all(row["pk"].startswith("T#tenant-a#") for row in rows_of(store))
