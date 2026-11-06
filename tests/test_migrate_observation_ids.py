from copy import deepcopy

from marketdata.contracts import observation_content_id, stable_id
from marketdata.queries import history
from marketdata.service import ingest
from scripts.demo_source_pipeline import local_pipeline
from scripts.migrate_observation_ids import apply_plan, build_plan, tenant_observations
from tests.conftest import NOW
from tests.test_source_lifecycle import recreate_source, transform_values


def old_row(store, message, job_id, tenant="tenant-a", price="20100000"):
    """Rewrite a stored observation the way the old job-and-row ids looked."""
    value = {**deepcopy(message), "tenant_id": tenant}
    value["record"]["price"] = price
    result = ingest(value, store, NOW)
    item = next(
        row
        for row in tenant_observations(store.observations, tenant)
        if str(row["price"]) == price and row["job_id"] == value["job_id"]
    )
    store.observations.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
    old_id = stable_id(tenant, item["source_id"], job_id, item["row_number"])
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
    assert rows[0]["observation_id"] == observation_content_id(
        "tenant-a", rows[0]["source_id"], rows[0]["source_sku"],
        rows[0]["observed_at"], rows[0]["price_basis"], rows[0]["price"],
    )
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


def ingest_fixture_rows(pipeline, values, job_id):
    pipeline.store.create_job("tenant-a", job_id, {
        "source_id": "api_supplier", "kind": "api", "status": "normalizing",
        "row_count": len(values),
    })
    for value in values:
        ingest({**value, "job_id": job_id}, pipeline.store, pipeline.now)


def test_current_generation_ids_survive_migration_and_reimport():
    with local_pipeline() as pipeline:
        pipeline.configure_sources()
        pipeline.dispatch()
        pipeline.run_sources("api")
        values = transform_values(pipeline)
        ingest_fixture_rows(pipeline, values, "first-import")
        initial = rows_of(pipeline.store)
        assert len(initial) == 3
        assert all("source_generation" not in row for row in initial)

        plan = plan_for(pipeline.store)
        assert plan["writes"] == [] and plan["deletes"] == []
        apply_plan(plan, pipeline.store.observations)
        ingest_fixture_rows(pipeline, values, "second-import")

        assert {row["sk"] for row in rows_of(pipeline.store)} == {
            row["sk"] for row in initial
        }
        assert len(pipeline.store.receipts("tenant-a")) == 6
        assert plan_for(pipeline.store)["writes"] == []
        assert plan_for(pipeline.store)["deletes"] == []


def test_identical_prices_from_recreated_source_stay_separate():
    with local_pipeline() as pipeline:
        pipeline.configure_sources()
        pipeline.dispatch()
        pipeline.run_sources("api")
        values = transform_values(pipeline)
        ingest_fixture_rows(pipeline, values, "old-generation")
        recreate_source(pipeline)
        ingest_fixture_rows(pipeline, values, "new-generation")
        initial = rows_of(pipeline.store)
        assert len(initial) == 6

        plan = plan_for(pipeline.store)
        assert plan["unique"] == 6
        assert plan["duplicate_groups"] == 0
        assert plan["writes"] == [] and plan["deletes"] == []
        apply_plan(plan, pipeline.store.observations)
        ingest_fixture_rows(pipeline, values, "repeat-new-generation")

        assert {row["sk"] for row in rows_of(pipeline.store)} == {
            row["sk"] for row in initial
        }


def test_explicit_generations_migrate_without_collapsing_lifetimes(store, message):
    ingest(message, store, NOW)
    template = rows_of(store)[0]
    content_id = template["observation_id"]
    rows = []
    for generation in ("old-generation", "new-generation"):
        for job_id in ("first-import", "second-import"):
            row_id = stable_id("tenant-a", template["source_id"], job_id, template["row_number"])
            rows.append({
                **template, "job_id": job_id, "source_generation": generation,
                "observation_id": row_id,
                "sk": f"{template['source_id']}#{template['observed_at']}#{row_id}",
            })

    plan = build_plan(rows, "tenant-a")

    assert plan["unique"] == 2
    assert plan["duplicate_groups"] == 2
    assert len(plan["deletes"]) == 4
    assert {row["observation_id"] for row in plan["writes"]} == {
        stable_id(generation, content_id) for generation in ("old-generation", "new-generation")
    }


def test_unknown_ids_without_generation_metadata_are_not_rewritten(store, message):
    ingest(message, store, NOW)
    original = rows_of(store)[0]
    opaque = {**original, "observation_id": "unknown-historical-id"}
    opaque["sk"] = f"{opaque['source_id']}#{opaque['observed_at']}#{opaque['observation_id']}"

    plan = build_plan([opaque], "tenant-a")

    assert plan["writes"] == [] and plan["deletes"] == []
