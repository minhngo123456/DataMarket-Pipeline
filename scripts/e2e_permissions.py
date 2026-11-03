"""Check Viewer permissions and tenant isolation through the real HTTP API of a live stack.

Prints the plan and writes nothing unless --yes is passed. --yes creates two Cognito test
users and one test tenant, then runs the checks and leaves them in place. --cleanup prints
what it would delete, and --cleanup --yes deletes it. Only the fixed test emails and the
fixed test tenant below are ever written or deleted. Passwords stay in memory; Cognito
never emails these users.

Usage:
    python -m scripts.e2e_permissions --stack marketdata-dev --region ap-southeast-1 \
        [--profile marketdata-dev] [--yes] [--cleanup]
"""

import argparse
import json
import secrets
import string
import sys
import urllib.error
import urllib.request

import boto3
from boto3.dynamodb.conditions import Attr, Key
from pycognito.aws_srp import AWSSRP

from marketdata.repository import DynamoStore, pk
from scripts.cleanup_smoke_tenants import key_of, s3_versions, scan_keys
from scripts.smoke_test_aws import get_stack_outputs

HOME_TENANT = "group07"
OTHER_TENANT = "e2e-tenant-b"
VIEWER_EMAIL = "e2e-viewer@example.invalid"
OTHER_ADMIN_EMAIL = "e2e-admin-b@example.invalid"
TEST_EMAILS = (VIEWER_EMAIL, OTHER_ADMIN_EMAIL)
MAX_MEMBERS = 5
REQUIRED_AUTH_FLOW = "ALLOW_USER_SRP_AUTH"

# Bodies are invalid on purpose wherever the route validates after its role check, so a
# broken check answers 400 instead of writing to the real tenant. /reports/trigger takes
# no body, so a broken check there would queue one real report.
VIEWER_DENIED = (
    ("POST", "/sources", {}, "admin_or_analyst_role_required"),
    ("POST", "/jobs", {}, "role_not_allowed"),
    ("PATCH", "/products/e2e-none/match", {"observation_id": "e2e-none"}, "role_not_allowed"),
    ("POST", "/members/invite", {}, "admin_role_required"),
    ("POST", "/reports/trigger", {}, "role_not_allowed"),
)
VIEWER_READS = ("/me", "/matrix", "/sources", "/reports", "/products")
OTHER_ADMIN_EMPTY_LISTS = ("/sources", "/products", "/matrix", "/reports", "/pending-skus")


def random_password():
    # The prefix guarantees every character class, whatever the pool requires of them.
    alphabet = string.ascii_letters + string.digits
    return "aA1!" + "".join(secrets.choice(alphabet) for _ in range(28))


def planned_writes():
    return [
        f"Cognito: create user {VIEWER_EMAIL} (invitation suppressed, random permanent password)",
        f"Cognito: create user {OTHER_ADMIN_EMAIL} (invitation suppressed, random permanent password)",
        f"ControlTable: put T#{OTHER_TENANT} / META (status=active)",
        f"ControlTable: put U#<viewer sub> / MEMBERSHIP -> {HOME_TENANT}",
        f"ControlTable: put T#{HOME_TENANT} / MEMBER#<viewer sub> (role=viewer)",
        f"ControlTable: put U#<admin-b sub> / MEMBERSHIP -> {OTHER_TENANT}",
        f"ControlTable: put T#{OTHER_TENANT} / MEMBER#<admin-b sub> (role=admin)",
    ]


def denied_problem(status, payload, reason):
    """Return why a response is not the expected role denial, or None when it is."""
    if status != 403:
        return f"expected 403, got {status}"
    message = (payload.get("error") or {}).get("message") if isinstance(payload, dict) else None
    if message != reason:
        return f"expected reason {reason}, got {message}"
    return None


def unmasked_sources(sources):
    """Return source ids whose header values a non-admin can read."""
    return [
        source.get("source_id")
        for source in sources
        if any(value != "***" for value in (source.get("headers") or {}).values())
    ]


def values_in(payload):
    if isinstance(payload, dict):
        for value in payload.values():
            yield from values_in(value)
    elif isinstance(payload, list):
        for value in payload:
            yield from values_in(value)
    else:
        yield payload


def leaked_ids(payload, ids):
    """Compare whole values: a source id such as "data" must not match the "data" key."""
    return sorted(set(ids).intersection(v for v in values_in(payload) if isinstance(v, str)))


def run_checks(viewer, other_admin, home_ids):
    """viewer and other_admin are callables (method, path, body=None) -> (status, payload)."""
    results = []

    def record(name, problem):
        results.append(("FAIL" if problem else "PASS", name, problem or ""))

    def skip(name, why):
        results.append(("SKIP", name, why))

    reads = {path: viewer("GET", path) for path in VIEWER_READS}
    for path, (status, _) in reads.items():
        record(f"viewer GET {path}", None if status == 200 else f"expected 200, got {status}")

    me = reads["/me"][1]
    record("viewer role is viewer", None if me.get("role") == "viewer" else f"got {me.get('role')}")

    sources = reads["/sources"][1].get("data", []) if reads["/sources"][0] == 200 else []
    if any(source.get("headers") for source in sources):
        unmasked = unmasked_sources(sources)
        record("viewer sees masked source headers", f"unmasked: {unmasked}" if unmasked else None)
    else:
        skip("viewer sees masked source headers", "no source in this tenant has headers")

    for method, path, body, reason in VIEWER_DENIED:
        status, payload = viewer(method, path, body)
        record(f"viewer denied {method} {path}", denied_problem(status, payload, reason))

    every_id = [value for ids in home_ids.values() for value in ids]

    status, payload = other_admin("GET", "/me")
    record("tenant-b admin role is admin", None if status == 200 and payload.get("role") == "admin" else f"{status}")

    for path in OTHER_ADMIN_EMPTY_LISTS:
        status, payload = other_admin("GET", path)
        if status != 200:
            record(f"tenant-b admin GET {path} is empty", f"expected 200, got {status}")
            continue
        leaks = leaked_ids(payload, every_id)
        rows = payload.get("data", [])
        record(
            f"tenant-b admin GET {path} is empty",
            f"leaked: {leaks}" if leaks else (f"{len(rows)} rows" if rows else None),
        )

    status, payload = other_admin("GET", "/members")
    emails = [member.get("email") for member in payload.get("data", [])] if status == 200 else []
    record(
        "tenant-b admin lists only itself",
        None if status == 200 and emails == [OTHER_ADMIN_EMAIL] else f"status {status}, {len(emails)} members",
    )

    probes = (
        ("reports", "GET /reports/<group07 id>", "/reports/{}"),
        ("jobs", "GET /jobs/<group07 id>/status", "/jobs/{}/status"),
    )
    for kind, name, template in probes:
        if not home_ids.get(kind):
            skip(f"tenant-b admin {name} is 404", f"{HOME_TENANT} has no {kind}")
            continue
        problems = []
        for value in home_ids[kind]:
            status, _ = other_admin("GET", template.format(value))
            if status != 404:
                problems.append(f"{value}: {status}")
        record(f"tenant-b admin {name} is 404", "; ".join(problems))

    if home_ids.get("products"):
        problems = []
        for value in home_ids["products"]:
            status, payload = other_admin("GET", f"/products/{value}/price-history")
            problem = denied_problem(status, payload, "product_unavailable")
            if problem:
                problems.append(f"{value}: {problem}")
        record("tenant-b admin reads no group07 price history", "; ".join(problems))
    else:
        skip("tenant-b admin reads no group07 price history", f"{HOME_TENANT} has no products")

    return results


def api_caller(endpoint, token):
    def call(method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            endpoint.rstrip("/") + path,
            data=data,
            method=method,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        try:
            return status, json.loads(raw) if raw else {}
        except ValueError:
            return status, {}

    return call


def home_tenant_ids(store, tenant=HOME_TENANT):
    """Ids the other tenant must never reach, read directly so they do not depend on the API."""
    prefixes = {"sources": "SOURCE#", "products": "PRODUCT#", "reports": "REPORT#", "jobs": "JOB#"}
    ids = {}
    for kind, prefix in prefixes.items():
        items = store.pages(
            store.control,
            KeyConditionExpression=Key("pk").eq(pk(tenant)) & Key("sk").begins_with(prefix),
        )
        ids[kind] = [item["sk"].removeprefix(prefix) for item in items]
    return ids


def member_records(store, tenant=HOME_TENANT):
    return list(store.pages(
        store.control,
        KeyConditionExpression=Key("pk").eq(pk(tenant)) & Key("sk").begins_with("MEMBER#"),
    ))


def user_sub(cognito, pool_id, email):
    try:
        user = cognito.admin_get_user(UserPoolId=pool_id, Username=email)
    except cognito.exceptions.UserNotFoundException:
        return None
    return next(a["Value"] for a in user["UserAttributes"] if a["Name"] == "sub")


def ensure_user(cognito, pool_id, email):
    """Create the user if needed and give it a fresh password; return (sub, password)."""
    if user_sub(cognito, pool_id, email) is None:
        cognito.admin_create_user(
            UserPoolId=pool_id,
            Username=email,
            MessageAction="SUPPRESS",
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
            ],
        )
    password = random_password()
    cognito.admin_set_user_password(UserPoolId=pool_id, Username=email, Password=password, Permanent=True)
    return user_sub(cognito, pool_id, email), password


def add_member(store, tenant, sub, role, email):
    store.control.put_item(Item={"pk": "U#" + sub, "sk": "MEMBERSHIP", "tenant_id": tenant, "status": "active"})
    store.put(tenant, "MEMBER#" + sub, {"role": role, "status": "active", "email": email})


def provision(store, cognito, pool_id):
    """Return {email: password} for the test users."""
    viewer_sub, viewer_password = ensure_user(cognito, pool_id, VIEWER_EMAIL)
    admin_sub, admin_password = ensure_user(cognito, pool_id, OTHER_ADMIN_EMAIL)
    store.put(OTHER_TENANT, "META", {"status": "active", "product_count": 0})
    add_member(store, HOME_TENANT, viewer_sub, "viewer", VIEWER_EMAIL)
    add_member(store, OTHER_TENANT, admin_sub, "admin", OTHER_ADMIN_EMAIL)
    return {VIEWER_EMAIL: viewer_password, OTHER_ADMIN_EMAIL: admin_password}


def id_token(cognito, pool_id, client_id, email, password):
    srp = AWSSRP(username=email, password=password, pool_id=pool_id, client_id=client_id, client=cognito)
    return srp.authenticate_user()["AuthenticationResult"]["IdToken"]


def build_cleanup_plan(store, cognito, pool_id, s3, buckets):
    subs = {email: user_sub(cognito, pool_id, email) for email in TEST_EMAILS}
    control = [
        key_of(item)
        for item in store.pages(store.control, KeyConditionExpression=Key("pk").eq(pk(OTHER_TENANT)))
    ]
    control += [key_of(item) for item in member_records(store) if item.get("email") in TEST_EMAILS]
    # By tenant as well as by sub, so a membership survives no longer than its Cognito user.
    memberships = scan_keys(store.control, Attr("sk").eq("MEMBERSHIP") & Attr("tenant_id").eq(OTHER_TENANT))
    control += [key_of(item) for item in memberships]
    for sub in subs.values():
        if sub:
            item = store.control.get_item(Key={"pk": "U#" + sub, "sk": "MEMBERSHIP"}).get("Item")
            if item:
                control.append(key_of(item))
    unique_control = list({(key["pk"], key["sk"]): key for key in control}.values())

    observations = scan_keys(
        store.observations,
        Attr("pk").eq(pk(OTHER_TENANT)) | Attr("pk").begins_with(pk(OTHER_TENANT) + "#"),
    )
    return {
        "users": [email for email, sub in subs.items() if sub],
        "control": unique_control,
        "observations": [key_of(item) for item in observations],
        "s3_versions": [(bucket, v) for bucket in buckets for v in s3_versions(s3, bucket, OTHER_TENANT)],
    }


def delete_cleanup_plan(plan, store, cognito, pool_id, s3):
    with store.control.batch_writer() as batch:
        for key in plan["control"]:
            batch.delete_item(Key=key)
    with store.observations.batch_writer() as batch:
        for key in plan["observations"]:
            batch.delete_item(Key=key)
    for bucket, version in plan["s3_versions"]:
        s3.delete_object(Bucket=bucket, **version)
    for email in plan["users"]:
        cognito.admin_delete_user(UserPoolId=pool_id, Username=email)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stack", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--yes", action="store_true", help="Write for real; the default only prints the plan")
    parser.add_argument("--cleanup", action="store_true", help="Remove the test users and tenant instead")
    args = parser.parse_args(argv)

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    outputs = get_stack_outputs(session.client("cloudformation"), args.stack)
    store = DynamoStore(
        outputs["ControlTable"],
        outputs["ObservationsTable"],
        resource=session.resource("dynamodb"),
        client=session.client("dynamodb"),
    )
    cognito = session.client("cognito-idp")
    pool_id, client_id = outputs["UserPoolId"], outputs["UserPoolClientId"]

    if args.cleanup:
        s3 = session.client("s3")
        plan = build_cleanup_plan(store, cognito, pool_id, s3, [outputs["RawBucket"], outputs["ReportsBucket"]])
        print("DELETING" if args.yes else "DRY RUN: nothing will be deleted")
        print(f"users={plan['users']} control={len(plan['control'])} "
              f"observations={len(plan['observations'])} s3_versions={len(plan['s3_versions'])}")
        if args.yes:
            delete_cleanup_plan(plan, store, cognito, pool_id, s3)
            print("Done.")
        return 0

    flows = cognito.describe_user_pool_client(UserPoolId=pool_id, ClientId=client_id)["UserPoolClient"]
    if REQUIRED_AUTH_FLOW not in flows.get("ExplicitAuthFlows", []):
        print(f"[ERROR]: app client does not allow {REQUIRED_AUTH_FLOW}", file=sys.stderr)
        return 2

    members = member_records(store)
    viewer_is_new = not any(member.get("email") == VIEWER_EMAIL for member in members)
    if len(members) + viewer_is_new > MAX_MEMBERS:
        print(f"[ERROR]: {HOME_TENANT} already has {len(members)} members", file=sys.stderr)
        return 2

    home_ids = home_tenant_ids(store)
    print(f"Stack {args.stack}: auth flow {REQUIRED_AUTH_FLOW} allowed, {HOME_TENANT} has {len(members)} members")
    print("Probing " + " ".join(f"{kind}={len(ids)}" for kind, ids in home_ids.items()))

    if not args.yes:
        print("DRY RUN: nothing was written. --yes would:")
        for line in planned_writes():
            print("  " + line)
        print("Then call the API as each user:")
        for path in VIEWER_READS:
            print(f"  viewer GET {path}")
        for method, path, _, reason in VIEWER_DENIED:
            print(f"  viewer {method} {path} -> 403 {reason}")
        for path in (*OTHER_ADMIN_EMPTY_LISTS, "/members"):
            print(f"  tenant-b admin GET {path} -> no {HOME_TENANT} data")
        print(f"  tenant-b admin GET each {HOME_TENANT} report and job -> 404, price history -> 403 product_unavailable")
        return 0

    passwords = provision(store, cognito, pool_id)
    endpoint = outputs["HttpApiEndpoint"]
    viewer = api_caller(endpoint, id_token(cognito, pool_id, client_id, VIEWER_EMAIL, passwords[VIEWER_EMAIL]))
    other_admin = api_caller(
        endpoint, id_token(cognito, pool_id, client_id, OTHER_ADMIN_EMAIL, passwords[OTHER_ADMIN_EMAIL])
    )

    results = run_checks(viewer, other_admin, home_ids)
    for status, name, detail in results:
        print(f"{status} {name}" + (f": {detail}" if detail else ""))
    counts = {status: sum(r[0] == status for r in results) for status in ("PASS", "FAIL", "SKIP")}
    print(" ".join(f"{status}={count}" for status, count in counts.items()))
    print("Test users and tenant are still in place; remove them with --cleanup --yes.")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
