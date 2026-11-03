import re

import boto3
import pytest

from scripts.e2e_permissions import (
    HOME_TENANT,
    OTHER_ADMIN_EMAIL,
    OTHER_TENANT,
    VIEWER_DENIED,
    VIEWER_EMAIL,
    build_cleanup_plan,
    delete_cleanup_plan,
    denied_problem,
    home_tenant_ids,
    leaked_ids,
    planned_writes,
    provision,
    random_password,
    run_checks,
    unmasked_sources,
    user_sub,
)

HOME_IDS = {"sources": ["vendor"], "products": ["phone"], "reports": ["rep-1"], "jobs": ["job-1"]}


def good_viewer(method, path, body=None):
    if method != "GET":
        reason = next(r for m, p, _, r in VIEWER_DENIED if (m, p) == (method, path))
        return 403, {"error": {"code": "access_denied", "message": reason}}
    if path == "/me":
        return 200, {"role": "viewer"}
    if path == "/sources":
        return 200, {"data": [{"source_id": "vendor", "headers": {"x-api-key": "***"}}]}
    return 200, {"data": []}


def good_other_admin(method, path, body=None):
    if path == "/me":
        return 200, {"role": "admin"}
    if path == "/members":
        return 200, {"data": [{"email": OTHER_ADMIN_EMAIL}]}
    if path.startswith(("/reports/", "/jobs/")):
        return 404, {"error": {"code": "not_found", "message": "gone"}}
    if path.endswith("/price-history"):
        return 200, {"total": 0, "data": []}
    return 200, {"tenant_id": OTHER_TENANT, "data": []}


def failures(results):
    return {name for status, name, _ in results if status == "FAIL"}


def test_random_password_meets_the_pool_policy():
    password = random_password()
    assert len(password) >= 12
    assert re.search("[a-z]", password) and re.search("[A-Z]", password) and re.search("[0-9]", password)
    assert password != random_password()


def test_denial_must_be_403_with_the_role_reason():
    denied = {"error": {"message": "role_not_allowed"}}
    assert denied_problem(403, denied, "role_not_allowed") is None
    assert "got 201" in denied_problem(201, {}, "role_not_allowed")
    # 403 for a missing row would pass a status-only check without proving the role check ran.
    assert "pending_row_unavailable" in denied_problem(
        403, {"error": {"message": "pending_row_unavailable"}}, "role_not_allowed"
    )


def test_unmasked_sources_reports_readable_header_values():
    sources = [
        {"source_id": "masked", "headers": {"a": "***"}},
        {"source_id": "open", "headers": {"a": "***", "b": "secret"}},
        {"source_id": "none"},
    ]
    assert unmasked_sources(sources) == ["open"]


def test_leaked_ids_compares_whole_values_not_keys():
    payload = {"data": [{"source_id": "vendor", "nested": ["phone-2"]}], "total": 1}
    assert leaked_ids(payload, ["vendor", "data", "phone"]) == ["vendor"]


def test_all_checks_pass_against_a_correct_api():
    results = run_checks(good_viewer, good_other_admin, HOME_IDS)
    assert failures(results) == set()
    assert not [r for r in results if r[0] == "SKIP"]


def test_viewer_allowed_to_write_fails_that_check():
    def viewer(method, path, body=None):
        if (method, path) == ("POST", "/reports/trigger"):
            return 202, {"status": "queued"}
        return good_viewer(method, path, body)

    assert failures(run_checks(viewer, good_other_admin, HOME_IDS)) == {"viewer denied POST /reports/trigger"}


def test_unmasked_header_fails_for_the_viewer():
    def viewer(method, path, body=None):
        if (method, path) == ("GET", "/sources"):
            return 200, {"data": [{"source_id": "vendor", "headers": {"x-api-key": "secret"}}]}
        return good_viewer(method, path, body)

    assert failures(run_checks(viewer, good_other_admin, HOME_IDS)) == {"viewer sees masked source headers"}


def test_no_headers_is_a_skip_not_a_pass():
    def viewer(method, path, body=None):
        if (method, path) == ("GET", "/sources"):
            return 200, {"data": [{"source_id": "vendor"}]}
        return good_viewer(method, path, body)

    results = run_checks(viewer, good_other_admin, HOME_IDS)
    assert ("SKIP", "viewer sees masked source headers") in [r[:2] for r in results]


@pytest.mark.parametrize(
    ("path", "response", "check"),
    [
        ("/sources", (200, {"data": [{"source_id": "vendor"}]}), "tenant-b admin GET /sources is empty"),
        ("/products", (200, {"data": [{"product_id": "other"}]}), "tenant-b admin GET /products is empty"),
        ("/reports/rep-1", (200, {"report_id": "rep-1"}), "tenant-b admin GET /reports/<group07 id> is 404"),
        ("/jobs/job-1/status", (200, {"job_id": "job-1"}), "tenant-b admin GET /jobs/<group07 id>/status is 404"),
        ("/products/phone/price-history", (200, {"total": 3}), "tenant-b admin reads no group07 price history"),
        ("/members", (200, {"data": [{"email": "a@b"}]}), "tenant-b admin lists only itself"),
    ],
)
def test_cross_tenant_read_fails_its_check(path, response, check):
    def other_admin(method, requested, body=None):
        return response if requested == path else good_other_admin(method, requested, body)

    assert failures(run_checks(good_viewer, other_admin, HOME_IDS)) == {check}


def test_missing_group07_ids_skip_instead_of_passing():
    results = run_checks(good_viewer, good_other_admin, {"sources": [], "products": [], "reports": [], "jobs": []})
    skipped = {name for status, name, _ in results if status == "SKIP"}
    assert skipped == {
        "tenant-b admin GET /reports/<group07 id> is 404",
        "tenant-b admin GET /jobs/<group07 id>/status is 404",
        "tenant-b admin reads no group07 price history",
    }


def test_plan_only_names_the_fixed_test_identities():
    plan = "\n".join(planned_writes())
    assert VIEWER_EMAIL in plan and OTHER_ADMIN_EMAIL in plan and OTHER_TENANT in plan
    assert "role=admin" not in next(line for line in planned_writes() if f"T#{HOME_TENANT}" in line)


@pytest.fixture
def world(store):
    cognito = boto3.client("cognito-idp")
    pool_id = cognito.create_user_pool(PoolName="pool", UsernameAttributes=["email"])["UserPool"]["Id"]
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="raw")
    s3.put_bucket_versioning(Bucket="raw", VersioningConfiguration={"Status": "Enabled"})
    store.put(HOME_TENANT, "META", {"status": "active"})
    store.put(HOME_TENANT, "SOURCE#vendor", {"kind": "csv"})
    store.put(HOME_TENANT, "MEMBER#real", {"role": "admin", "status": "active", "email": "real@example.com"})
    store.control.put_item(Item={"pk": "U#real", "sk": "MEMBERSHIP", "tenant_id": HOME_TENANT, "status": "active"})
    store.observations.put_item(Item={"pk": f"T#{HOME_TENANT}#P#phone", "sk": "keep", "tenant_id": HOME_TENANT})
    # Longer id sharing the prefix: cleanup must not spill into it.
    store.observations.put_item(Item={"pk": f"T#{OTHER_TENANT}0#P#phone", "sk": "keep", "tenant_id": "x"})
    s3.put_object(Bucket="raw", Key=f"tenants/{HOME_TENANT}/raw/job", Body=b"keep")
    return store, cognito, pool_id, s3


def test_provision_gives_each_user_the_expected_role_and_tenant(world):
    store, cognito, pool_id, _ = world
    passwords = provision(store, cognito, pool_id)
    assert set(passwords) == {VIEWER_EMAIL, OTHER_ADMIN_EMAIL}

    viewer = user_sub(cognito, pool_id, VIEWER_EMAIL)
    admin = user_sub(cognito, pool_id, OTHER_ADMIN_EMAIL)
    assert store.control.get_item(Key={"pk": "U#" + viewer, "sk": "MEMBERSHIP"})["Item"]["tenant_id"] == HOME_TENANT
    assert store.get(HOME_TENANT, "MEMBER#" + viewer)["role"] == "viewer"
    assert store.control.get_item(Key={"pk": "U#" + admin, "sk": "MEMBERSHIP"})["Item"]["tenant_id"] == OTHER_TENANT
    assert store.get(OTHER_TENANT, "MEMBER#" + admin)["role"] == "admin"
    # The test admin must never gain a role in the real tenant.
    assert store.get(HOME_TENANT, "MEMBER#" + admin) is None


def test_provision_twice_keeps_one_user_each(world):
    store, cognito, pool_id, _ = world
    provision(store, cognito, pool_id)
    first = user_sub(cognito, pool_id, VIEWER_EMAIL)
    provision(store, cognito, pool_id)
    assert user_sub(cognito, pool_id, VIEWER_EMAIL) == first
    assert len(cognito.list_users(UserPoolId=pool_id)["Users"]) == 2


def test_cleanup_removes_only_test_data(world):
    store, cognito, pool_id, s3 = world
    provision(store, cognito, pool_id)
    viewer = user_sub(cognito, pool_id, VIEWER_EMAIL)
    store.put(OTHER_TENANT, "SOURCE#stray", {"kind": "csv"})
    store.observations.put_item(Item={"pk": f"T#{OTHER_TENANT}#P#phone", "sk": "drop", "tenant_id": OTHER_TENANT})
    s3.put_object(Bucket="raw", Key=f"tenants/{OTHER_TENANT}/raw/job", Body=b"drop")

    plan = build_cleanup_plan(store, cognito, pool_id, s3, ["raw"])
    assert set(plan["users"]) == {VIEWER_EMAIL, OTHER_ADMIN_EMAIL}
    delete_cleanup_plan(plan, store, cognito, pool_id, s3)

    assert cognito.list_users(UserPoolId=pool_id)["Users"] == []
    assert store.get(HOME_TENANT, "MEMBER#" + viewer) is None
    assert "Item" not in store.control.get_item(Key={"pk": "U#" + viewer, "sk": "MEMBERSHIP"})
    assert store.get(OTHER_TENANT, "META") is None
    assert store.get(OTHER_TENANT, "SOURCE#stray") is None

    assert store.get(HOME_TENANT, "META")
    assert store.get(HOME_TENANT, "SOURCE#vendor")
    assert store.get(HOME_TENANT, "MEMBER#real")
    assert "Item" in store.control.get_item(Key={"pk": "U#real", "sk": "MEMBERSHIP"})
    remaining = {item["pk"] for item in store.observations.scan()["Items"]}
    assert f"T#{OTHER_TENANT}#P#phone" not in remaining
    assert {f"T#{HOME_TENANT}#P#phone", f"T#{OTHER_TENANT}0#P#phone"} <= remaining
    keys = {v["Key"] for v in s3.list_object_versions(Bucket="raw").get("Versions", [])}
    assert keys == {f"tenants/{HOME_TENANT}/raw/job"}


def test_cleanup_after_a_lost_cognito_user_still_finds_its_records(world):
    store, cognito, pool_id, s3 = world
    provision(store, cognito, pool_id)
    viewer = user_sub(cognito, pool_id, VIEWER_EMAIL)
    cognito.admin_delete_user(UserPoolId=pool_id, Username=VIEWER_EMAIL)

    plan = build_cleanup_plan(store, cognito, pool_id, s3, ["raw"])
    assert plan["users"] == [OTHER_ADMIN_EMAIL]
    assert {"pk": f"T#{HOME_TENANT}", "sk": "MEMBER#" + viewer} in plan["control"]


def test_home_tenant_ids_groups_by_kind(world):
    store, *_ = world
    store.put(HOME_TENANT, "REPORT#rep-1", {"status": "done"})
    store.put(HOME_TENANT, "REPORT_DAILY#2026", {"status": "done"})
    ids = home_tenant_ids(store)
    assert ids["sources"] == ["vendor"]
    assert ids["reports"] == ["rep-1"]
