"""Create isolated fixtures and test real Cognito SRP login and HTTP API authorization."""

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import BotoCoreError, ClientError

from marketdata.contracts import iso
from marketdata.repository import DynamoStore, pk
from scripts.verify_aws import CONFIG, new_session, write_report

ROOT = Path(__file__).resolve().parents[1]


def node_check(node, payload, runner=subprocess.run):
    """Pass ephemeral passwords over stdin; never include subprocess output in an error."""
    process = runner(
        [node, str(ROOT / "apps/web/scripts/smoke-aws-api.mjs")],
        input=json.dumps(payload), text=True, encoding="utf-8", capture_output=True,
        timeout=300, cwd=ROOT,
    )
    try:
        result = json.loads(process.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("node_smoke_did_not_return_json") from exc
    if result.get("status") not in ("passed", "failed"):
        raise RuntimeError("invalid_node_smoke_result")
    if process.returncode and result.get("status") == "passed":
        raise RuntimeError("node_smoke_exit_status_mismatch")
    if result["status"] == "passed" and (
        len(result.get("checks", [])) != 15 or not all(c.get("passed") is True for c in result["checks"])
    ):
        raise RuntimeError("node_smoke_checks_missing")
    # Keep only known evidence fields; tokens or credentials must not reach disk.
    return {"status": result["status"], "mode": result.get("mode"),
            "checks": [{k: c[k] for k in ("name", "passed", "expected_status", "actual_status") if k in c}
                       for c in result.get("checks", [])],
            "error_type": result.get("error_type")}


def cleanup_fixtures(store, cognito, pool_id, fixtures):
    """Delete only random tenants and Cognito users created by this invocation."""
    errors = []
    for tenant in fixtures["tenants"]:
        try:
            rows = list(store.pages(store.control,
                KeyConditionExpression=Key("pk").eq(pk(tenant)), ConsistentRead=True))
            for row in rows:
                store.control.delete_item(Key={"pk": row["pk"], "sk": row["sk"]})
        except (BotoCoreError, ClientError) as exc:
            errors.append({"resource": "tenant_partition", "error_type": type(exc).__name__})
    for user in fixtures["users"]:
        try:
            if user.get("sub"):
                store.control.delete_item(
                    Key={"pk": "U#" + user["sub"], "sk": "MEMBERSHIP"},
                    ConditionExpression=Attr("tenant_id").eq(user["tenant_id"]),
                )
        except store.control.meta.client.exceptions.ConditionalCheckFailedException:
            pass
        except (BotoCoreError, ClientError) as exc:
            errors.append({"resource": "membership", "error_type": type(exc).__name__})
        try:
            cognito.admin_delete_user(UserPoolId=pool_id, Username=user["username"])
        except cognito.exceptions.UserNotFoundException:
            pass
        except (BotoCoreError, ClientError) as exc:
            errors.append({"resource": "cognito_user", "error_type": type(exc).__name__})
    return {"requested": True, "status": "failed" if errors else "completed", "errors": errors}


def run_api_smoke(session, stack_name, node, report, cleanup=False):
    cfn = session.client("cloudformation", config=CONFIG)
    stack = cfn.describe_stacks(StackName=stack_name)["Stacks"][0]
    if stack["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
        raise RuntimeError("stack_not_ready")
    outputs = {x["OutputKey"]: x["OutputValue"] for x in stack.get("Outputs", [])}
    pool_id = outputs["UserPoolId"]
    cognito = session.client("cognito-idp", config=CONFIG)
    store = DynamoStore(outputs["ControlTable"], outputs["ObservationsTable"],
                        resource=session.resource("dynamodb", config=CONFIG),
                        client=session.client("dynamodb", config=CONFIG))
    suffix = uuid.uuid4().hex[:12]
    tenants = [f"smokeapi-a-{suffix}", f"smokeapi-b-{suffix}"]
    owned = {"tenants": [], "users": []}
    report["fixtures"] = owned
    expires = int((datetime.now(UTC) + timedelta(days=1)).timestamp())
    credentials = {}
    try:
        for tenant in tenants:
            store.control.put_item(Item={"pk": pk(tenant), "sk": "META", "status": "active",
                "product_count": 0, "expires_at": expires}, ConditionExpression=Attr("pk").not_exists())
            owned["tenants"].append(tenant)
            store.put(tenant, "SOURCE#vendor", {"source_id": "vendor", "kind": "csv", "enabled": True})
            store.create_product(tenant, "phone", "API smoke product")
        for name, tenant, role in (("admin_a", tenants[0], "admin"), ("viewer_a", tenants[0], "viewer"),
                                   ("admin_b", tenants[1], "admin")):
            username = f"hoang-smoke-{suffix}-{name}@example.invalid"
            password = secrets.token_urlsafe(24) + "aA1!"
            user = cognito.admin_create_user(UserPoolId=pool_id, Username=username,
                UserAttributes=[{"Name": "email", "Value": username}], MessageAction="SUPPRESS")["User"]
            # Track the canonical Cognito username returned by our successful create.
            owned_user = {"username": user["Username"], "tenant_id": tenant}
            owned["users"].append(owned_user)
            sub = next(a["Value"] for a in user["Attributes"] if a["Name"] == "sub")
            owned_user["sub"] = sub
            cognito.admin_set_user_password(UserPoolId=pool_id, Username=user["Username"],
                                           Password=password, Permanent=True)
            store.save_member(tenant, sub, role, username)
            credentials[name] = {"username": username, "password": password, "sub": sub}
        job_id, report_id, observation_id = (f"job-{suffix}", f"report-{suffix}", f"pending-{suffix}")
        store.create_job(tenants[0], job_id, {"source_id": "vendor", "kind": "csv", "status": "queued"})
        store.put(tenants[0], "REPORT#" + report_id,
                  {"report_id": report_id, "s3_key": f"tenants/{tenants[0]}/reports/{report_id}.json"})
        store.put(tenants[0], "RECEIPT#" + observation_id, {"observation_id": observation_id,
            "tenant_id": tenants[0], "source_id": "vendor", "job_id": job_id, "status": "pending",
            "source_sku": "UNMATCHED", "price": "100", "currency": "VND",
            "observed_at": iso(datetime.now(UTC)), "expires_at": expires})
        payload = {"apiUrl": outputs["HttpApiEndpoint"], "userPoolId": pool_id,
            "userPoolClientId": outputs["UserPoolClientId"], "users": credentials,
            "fixtures": {"tenantA": tenants[0], "tenantB": tenants[1], "jobId": job_id,
                "reportId": report_id, "observationId": observation_id, "proofProductId": "proof-" + suffix}}
        result = node_check(node, payload)
        report.update(result)
        report["auth_flow"] = "USER_SRP_AUTH"
        report["api_endpoint"] = outputs["HttpApiEndpoint"]
        report["not_tested"] = ["browser UI", "API/web collection", "AI", "SNS/Slack delivery", "report download"]
    finally:
        credentials.clear()
        report["cleanup"] = cleanup_fixtures(store, cognito, pool_id, owned) if cleanup else {
            "requested": False, "status": "retained_for_inspection",
            "note": "Passwords were not saved. Remove only the listed random tenant partitions and users."}
        if report["cleanup"]["status"] == "failed":
            report["status"] = "failed"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.getenv("AWS_PROFILE", "marketdata-dev"))
    parser.add_argument("--region", default=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
    parser.add_argument("--stack", default="marketdata-hoang-dev")
    parser.add_argument("--node", default=shutil.which("node"))
    parser.add_argument("--write-fixtures", action="store_true", help="Required: create isolated dev data and Cognito users; no email")
    parser.add_argument("--cleanup", action="store_true", help="Remove only this run's fixtures in finally")
    parser.add_argument("--output", type=Path, default=Path("build/evidence/aws-api-smoke.json"))
    args = parser.parse_args(argv)
    if not args.write_fixtures:
        parser.error("--write-fixtures is required before any AWS call")
    if not args.node:
        parser.error("Node.js is required; pass --node with its executable path")
    report = {"status": "running", "mode": "live_aws_api_smoke", "stack_name": args.stack,
              "region": args.region, "checked_at": datetime.now(UTC).isoformat()}
    try:
        run_api_smoke(new_session(args.profile, args.region), args.stack, args.node, report, args.cleanup)
    except (BotoCoreError, ClientError, RuntimeError, KeyError, ValueError, StopIteration,
            subprocess.SubprocessError, OSError) as exc:
        report["status"] = "failed"
        report["error_type"] = type(exc).__name__
    write_report(report, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
