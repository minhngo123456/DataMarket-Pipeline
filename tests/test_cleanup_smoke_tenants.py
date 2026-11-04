import boto3
import pytest

from scripts.cleanup_smoke_tenants import build_plan, delete_plan, tenant_from_pk

SMOKE = "smoke-a-12345678"
LOOK_ALIKES = ("smoke-a-1234567", "smoke-c-12345678", "smoke-a-1234567890", "smoke-a-XYZ45678")


def put_observation(store, tenant):
    store.observations.put_item(
        Item={"pk": f"T#{tenant}#P#phone", "sk": "vendor#2026-10-19T00:30:00Z#obs-1", "tenant_id": tenant}
    )


@pytest.fixture
def world(store):
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="raw")
    s3.put_bucket_versioning(Bucket="raw", VersioningConfiguration={"Status": "Enabled"})
    for tenant in (SMOKE, *LOOK_ALIKES):
        store.put(tenant, "META", {"status": "active"})
        store.put(tenant, "SOURCE#vendor", {"kind": "csv"})
        put_observation(store, tenant)
        s3.put_object(Bucket="raw", Key=f"tenants/{tenant}/raw/job", Body=b"one")
    s3.put_object(Bucket="raw", Key=f"tenants/{SMOKE}/raw/job", Body=b"two")
    s3.delete_object(Bucket="raw", Key=f"tenants/{SMOKE}/raw/job")
    # Longer id sharing the prefix: the tenant match must not spill into it.
    s3.put_object(Bucket="raw", Key=f"tenants/{SMOKE}0/raw/job", Body=b"x")
    s3.put_object(Bucket="raw", Key="tenants/tenant-a/raw/job", Body=b"real")
    put_observation(store, "tenant-a")
    store.control.put_item(Item={"pk": "U#smoke-user", "sk": "MEMBERSHIP", "tenant_id": SMOKE})
    store.control.put_item(Item={"pk": "U#real-user", "sk": "MEMBERSHIP", "tenant_id": "tenant-a"})
    store.control.put_item(Item={"pk": "U#odd-user", "sk": "MEMBERSHIP", "tenant_id": LOOK_ALIKES[0]})
    return store, s3


def all_versions(s3):
    response = s3.list_object_versions(Bucket="raw")
    return response.get("Versions", []) + response.get("DeleteMarkers", [])


def test_current_smoke_id_length_matches():
    assert tenant_from_pk("T#smoke-a-38619e63121f") == "smoke-a-38619e63121f"
    assert tenant_from_pk("T#smoke-b-38619e63121f#P#phone") == "smoke-b-38619e63121f"
    assert tenant_from_pk("T#smoke-a-38619e63121") is None
    assert tenant_from_pk("T#smoke-a-38619e63121f0") is None


def test_only_exact_smoke_ids_match():
    assert tenant_from_pk(f"T#{SMOKE}") == SMOKE
    assert tenant_from_pk(f"T#{SMOKE}#P#phone") == SMOKE
    for tenant in (*LOOK_ALIKES, "tenant-a", "group07"):
        assert tenant_from_pk(f"T#{tenant}") is None
    assert tenant_from_pk(f"U#{SMOKE}") is None


def test_plan_counts_without_deleting(world):
    store, s3 = world
    versions_before = len(all_versions(s3))
    plan = build_plan(store.control, store.observations, s3, ["raw"])

    assert list(plan) == [SMOKE]
    assert len(plan[SMOKE]["control"]) == 2
    assert len(plan[SMOKE]["memberships"]) == 1
    assert len(plan[SMOKE]["observations"]) == 1
    assert len(plan[SMOKE]["s3_versions"]) == 3  # two versions and a delete marker

    assert store.get(SMOKE, "META") is not None
    assert len(all_versions(s3)) == versions_before


def test_delete_removes_only_the_smoke_tenant(world):
    store, s3 = world
    delete_plan(build_plan(store.control, store.observations, s3, ["raw"]), store.control, store.observations, s3)

    assert store.get(SMOKE, "META") is None
    assert "Item" not in store.control.get_item(Key={"pk": "U#smoke-user", "sk": "MEMBERSHIP"})
    assert store.observations.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("pk").eq(f"T#{SMOKE}#P#phone")
    )["Items"] == []
    remaining = {v["Key"] for v in all_versions(s3)}
    assert not any(key.startswith(f"tenants/{SMOKE}/") for key in remaining)

    for tenant in (*LOOK_ALIKES, "tenant-a", "tenant-b"):
        assert store.get(tenant, "META") is not None
    assert "Item" in store.control.get_item(Key={"pk": "U#real-user", "sk": "MEMBERSHIP"})
    assert "Item" in store.control.get_item(Key={"pk": "U#odd-user", "sk": "MEMBERSHIP"})
    assert {f"tenants/{SMOKE}0/raw/job", "tenants/tenant-a/raw/job"} <= remaining
    assert store.observations.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("pk").eq("T#tenant-a#P#phone")
    )["Items"] != []
