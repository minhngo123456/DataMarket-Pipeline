"""Prepare, deploy and operate the demo guard without deleting pipeline data."""

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "build/evidence/demo-guard-plan.json"
CONFIG = Config(connect_timeout=5, read_timeout=30,
                retries={"mode": "standard", "total_max_attempts": 3})
READY = {"CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE"}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")


def client(session, service):
    return session.client(service, config=CONFIG)


def checked_session(args):
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    identity = client(session, "sts").get_caller_identity()
    if identity["Account"] != args.account:
        raise ValueError(f"Wrong AWS account: expected {args.account}, got {identity['Account']}")
    if args.region != "us-east-1" or args.target_stack != "marketdata-hoang-dev":
        raise ValueError("This guard is limited to marketdata-hoang-dev in us-east-1")
    if args.guard_stack == args.target_stack:
        raise ValueError("Guard and pipeline stacks must be separate")
    return session


def stack_parameters(stack):
    return {item["ParameterKey"]: item["ParameterValue"] for item in stack.get("Parameters", [])}


def stack_outputs(stack):
    return {item["OutputKey"]: item["OutputValue"] for item in stack.get("Outputs", [])}


def find_guard(cfn, name):
    matches = [item for page in cfn.get_paginator("list_stacks").paginate()
               for item in page["StackSummaries"]
               if item["StackName"] == name and item["StackStatus"] != "DELETE_COMPLETE"]
    if not matches:
        return None
    return cfn.describe_stacks(StackName=matches[0]["StackId"])["Stacks"][0]


def target_inventory(session, args):
    cfn, lambdas = client(session, "cloudformation"), client(session, "lambda")
    scheduler, events = client(session, "scheduler"), client(session, "events")
    stack = cfn.describe_stacks(StackName=args.target_stack)["Stacks"][0]
    if stack["StackStatus"] not in READY:
        raise ValueError(f"Pipeline stack is not ready: {stack['StackStatus']}")
    expected = f"arn:aws:cloudformation:{args.region}:{args.account}:stack/{args.target_stack}/"
    if not stack["StackId"].startswith(expected):
        raise ValueError("Unexpected target stack ARN")
    resources = [item for page in cfn.get_paginator("list_stack_resources").paginate(
        StackName=stack["StackId"]) for item in page["StackResourceSummaries"]
        if item.get("PhysicalResourceId") and not item["ResourceStatus"].startswith("DELETE_")]
    groups = [r["PhysicalResourceId"] for r in resources
              if r["ResourceType"] == "AWS::Scheduler::ScheduleGroup"]
    if len(groups) > 1:
        raise ValueError("Expected at most one pipeline scheduler group")
    group = groups[0] if groups else "default"
    scope = {key: [] for key in ("FunctionArns", "MappingArns", "ScheduleArns",
                                "ScheduleRoleArns", "RuleArns")}
    for item in resources:
        kind, physical = item["ResourceType"], item["PhysicalResourceId"]
        if kind == "AWS::Lambda::Function":
            info = lambdas.get_function_configuration(FunctionName=physical)
            scope["FunctionArns"].append(info["FunctionArn"])
        elif kind == "AWS::Lambda::EventSourceMapping":
            info = lambdas.get_event_source_mapping(UUID=physical)
            scope["MappingArns"].append(info.get("EventSourceMappingArn") or
                                      f"arn:aws:lambda:{args.region}:{args.account}:event-source-mapping:{physical}")
        elif kind == "AWS::Scheduler::Schedule":
            if physical.startswith("arn:"):
                schedule_group, name = physical.split(":schedule/", 1)[1].split("/", 1)
            elif "|" in physical:
                schedule_group, name = physical.split("|", 1)
            else:
                schedule_group, name = group, physical
            info = scheduler.get_schedule(Name=name, GroupName=schedule_group)
            scope["ScheduleArns"].append(info["Arn"])
            scope["ScheduleRoleArns"].append(info["Target"]["RoleArn"])
        elif kind == "AWS::Events::Rule":
            scope["RuleArns"].append(events.describe_rule(Name=physical)["Arn"])
    if any(not values for values in scope.values()):
        raise ValueError("Missing pipeline resource type; refusing to build an incomplete guard")
    return stack, {key: sorted(set(values)) for key, values in scope.items()}


def prepare(session, args):
    cfn = client(session, "cloudformation")
    stack, scope = target_inventory(session, args)
    existing = find_guard(cfn, args.guard_stack)
    if existing and existing["StackStatus"] not in READY | {"REVIEW_IN_PROGRESS"}:
        raise ValueError(f"Guard stack is not ready: {existing['StackStatus']}")
    limits = client(session, "lambda").get_account_settings()["AccountLimit"]
    if limits["ConcurrentExecutions"] < 1:
        raise ValueError("AWS account has no Lambda execution capacity")
    now = datetime.now(UTC).replace(microsecond=0)
    deadline = now + timedelta(days=7)
    params = {
        "TargetStackId": stack["StackId"], "AlertEmail": args.email,
        "BudgetName": f"{args.guard_stack}-demo",
        "BudgetStartUtc": now.replace(hour=0, minute=0, second=0).isoformat().replace("+00:00", "Z"),
        "BudgetEndUtc": (deadline + timedelta(days=4)).replace(hour=0, minute=0, second=0).isoformat().replace("+00:00", "Z"),
        "DeadlineUtc": deadline.isoformat().replace("+00:00", "Z"),
        "DeadlineExpression": f"at({deadline.strftime('%Y-%m-%dT%H:%M:%S')})",
        **{key: ",".join(values) for key, values in scope.items()},
    }
    if existing:
        previous = stack_parameters(existing)
        for key in ("BudgetName", "BudgetStartUtc", "BudgetEndUtc", "DeadlineUtc", "DeadlineExpression"):
            if key in previous:
                params[key] = previous[key]
        if previous.get("TargetStackId", stack["StackId"]) != stack["StackId"]:
            raise ValueError("Existing guard belongs to another target stack incarnation")
    archive = ROOT / "build/demo-guard/function.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    source = (ROOT / "services/demo_guard/app.py").read_bytes()
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as package:
        entry = ZipInfo("app.py", date_time=(1980, 1, 1, 0, 0, 0))
        entry.compress_type = ZIP_DEFLATED
        package.writestr(entry, source)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    code_key = f"demo-guard/{digest}.zip"
    client(session, "s3").upload_file(str(archive), args.artifact_bucket, code_key,
                                    ExtraArgs={"ServerSideEncryption": "AES256"})
    params.update(CodeBucket=args.artifact_bucket, CodeKey=code_key)
    change_name = f"demo-guard-{now.strftime('%Y%m%dT%H%M%S')}"
    request = {
        "StackName": args.guard_stack, "ChangeSetName": change_name,
        "ChangeSetType": "UPDATE" if existing and existing["StackStatus"] != "REVIEW_IN_PROGRESS" else "CREATE",
        "TemplateBody": (ROOT / "infra/aws/demo-guard.yaml").read_text(encoding="utf-8"),
        "Parameters": [{"ParameterKey": key, "ParameterValue": value} for key, value in params.items()],
        "Capabilities": ["CAPABILITY_IAM"],
        "Tags": [{"Key": "Project", "Value": "MarketData"}, {"Key": "Purpose", "Value": "demo-guard"}],
    }
    response = cfn.create_change_set(**request)
    plan = {"account": args.account, "region": args.region, "target_stack": args.target_stack,
            "target_stack_id": stack["StackId"], "guard_stack": args.guard_stack,
            "change_set_id": response["Id"], "change_set_type": request["ChangeSetType"],
            "created_at": now.isoformat(),
            "parameters": params, "lambda_execution_limit": limits["ConcurrentExecutions"],
            "resource_counts": {key: len(value) for key, value in scope.items()}}
    write_json(PLAN_PATH, plan)
    return {"plan": str(PLAN_PATH), "change_set_id": response["Id"],
            "deadline_utc": params["DeadlineUtc"], "resource_counts": plan["resource_counts"],
            "message": "Change set created for review; no guard resources deployed yet."}


def apply_plan(session, args):
    plan = load_plan(args)

    cfn = client(session, "cloudformation")
    target = cfn.describe_stacks(StackName=args.target_stack)["Stacks"][0]
    if target["StackId"] != plan["target_stack_id"] or target["StackStatus"] not in READY:
        raise ValueError("Target stack changed or is not ready; prepare a new plan")
    change = cfn.describe_change_set(ChangeSetName=plan["change_set_id"])
    if change["Status"] != "CREATE_COMPLETE" or change["ExecutionStatus"] != "AVAILABLE":
        raise ValueError(f"Change set is not executable: {change['Status']} / {change['ExecutionStatus']}")
    if any(item["ResourceChange"]["Action"] != "Add" for item in change.get("Changes", [])) and plan["change_set_type"] == "CREATE":
        raise ValueError("New guard unexpectedly modifies or removes existing resources")
    cfn.execute_change_set(ChangeSetName=plan["change_set_id"])
    return {"guard_stack": args.guard_stack, "change_set_id": plan["change_set_id"],
            "message": "Guard deployment started; check stack status before verify."}


def load_plan(args):
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    for key in ("account", "region", "guard_stack", "target_stack"):
        if plan[key] != getattr(args, key):
            raise ValueError(f"Plan does not match --{key.replace('_', '-')}")
    return plan


def guard_stack(session, args):
    stack = client(session, "cloudformation").describe_stacks(StackName=args.guard_stack)["Stacks"][0]
    params = stack_parameters(stack)
    if stack["StackStatus"] not in READY:
        raise ValueError(f"Guard stack is not ready: {stack['StackStatus']}")
    target = client(session, "cloudformation").describe_stacks(StackName=args.target_stack)["Stacks"][0]
    if params["TargetStackId"] != target["StackId"]:
        raise ValueError("Guard does not belong to the current target stack")
    return stack, params, stack_outputs(stack)


def invoke_guard(session, arn, event):
    invoke_client = session.client("lambda", config=Config(
        connect_timeout=5, read_timeout=135,
        retries={"mode": "standard", "total_max_attempts": 1}))
    response = invoke_client.invoke(FunctionName=arn, InvocationType="RequestResponse",
                                    Payload=json.dumps(event).encode())
    payload = json.loads(response["Payload"].read())
    if response.get("FunctionError"):
        raise RuntimeError(f"Guard invocation failed: {json.dumps(payload)}")
    return payload


def utc_time(value):
    return value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))


def allows_service(policy, service, action, resource, account, source_arn):
    def values(value):
        return value if isinstance(value, list) else [value]

    for statement in policy.get("Statement", []):
        if statement.get("Effect") != "Allow":
            continue
        principal = statement.get("Principal", {})
        if not isinstance(principal, dict) or values(principal.get("Service")) != [service]:
            continue
        if [item.lower() for item in values(statement.get("Action", []))] != [action.lower()]:
            continue
        if values(statement.get("Resource", [])) != [resource]:
            continue
        conditions = {(operator.lower(), key.lower()): value
                      for operator, entries in statement.get("Condition", {}).items()
                      for key, value in entries.items()}
        account_matches = conditions.get(("stringequals", "aws:sourceaccount")) == account
        arn_matches = any(conditions.get((operator, "aws:sourcearn")) == source_arn
                          for operator in ("arnequals", "arnlike"))
        if account_matches and arn_matches:
            return True
    return False


def verify(session, args):
    stack, params, outputs = guard_stack(session, args)
    plan = load_plan(args)
    if plan["target_stack_id"] != params["TargetStackId"]:
        raise ValueError("Saved plan and deployed guard target different stack incarnations")
    expected_email = plan["parameters"]["AlertEmail"]
    budgets, sns = client(session, "budgets"), client(session, "sns")
    budget = budgets.describe_budget(AccountId=args.account, BudgetName=outputs["BudgetName"])["Budget"]
    notifications = budgets.describe_notifications_for_budget(
        AccountId=args.account, BudgetName=outputs["BudgetName"])["Notifications"]
    subscribers = [{"notification": notice, "subscribers": budgets.describe_subscribers_for_notification(
        AccountId=args.account, BudgetName=outputs["BudgetName"], Notification=notice)["Subscribers"]}
        for notice in notifications]
    schedule = client(session, "scheduler").get_schedule(
        Name=outputs["DeadlineScheduleName"], GroupName=outputs["DeadlineScheduleGroup"])
    topic = sns.get_topic_attributes(TopicArn=outputs["StopTopicArn"])["Attributes"]
    subscriptions = [item for page in sns.get_paginator("list_subscriptions_by_topic").paginate(
        TopicArn=outputs["StopTopicArn"]) for item in page["Subscriptions"]]
    policy = json.loads(client(session, "lambda").get_policy(
        FunctionName=outputs["GuardFunctionArn"])["Policy"])
    dry_run = invoke_guard(session, outputs["GuardFunctionArn"], {"action": "dry_run", "source": "manual"})
    by_threshold = {notice["notification"]["Threshold"]: notice for notice in subscribers}
    warning = by_threshold.get(5, {}).get("subscribers", [])
    stop = by_threshold.get(8, {}).get("subscribers", [])
    limit = by_threshold.get(10, {}).get("subscribers", [])
    email_subscriber = {"SubscriptionType": "EMAIL", "Address": expected_email}
    expected_budget_arn = f"arn:aws:budgets::{args.account}:budget/{outputs['BudgetName']}"
    checks = {
        "budget_10_usd": Decimal(budget["BudgetLimit"]["Amount"]) == Decimal("10")
        and budget["BudgetLimit"]["Unit"] == "USD",
        "cost_before_credits": budget["CostTypes"]["IncludeCredit"] is False
        and budget["CostTypes"]["IncludeRefund"] is False,
        "custom_budget_period": budget["TimeUnit"] == "CUSTOM",
        "budget_period_matches": all(utc_time(budget["TimePeriod"][key]) == utc_time(params[param])
                                     for key, param in (("Start", "BudgetStartUtc"), ("End", "BudgetEndUtc"))),
        "account_wide_cost_scope": not budget.get("CostFilters") and not budget.get("FilterExpression"),
        "actual_absolute_thresholds": set(by_threshold) == {5, 8, 10} and all(
            notice["NotificationType"] == "ACTUAL" and notice["ComparisonOperator"] == "GREATER_THAN"
            and notice["ThresholdType"] == "ABSOLUTE_VALUE" for notice in notifications),
        "warning_email_only": warning == [email_subscriber],
        "stop_sns": {"SubscriptionType": "SNS", "Address": outputs["StopTopicArn"]} in stop,
        "stop_email": email_subscriber in stop,
        "limit_email_only": limit == [email_subscriber],
        "deadline_enabled": schedule["State"] == "ENABLED",
        "deadline_target": schedule["Target"]["Arn"] == outputs["GuardFunctionArn"],
        "deadline_expression": schedule["ScheduleExpression"] == params["DeadlineExpression"],
        "deadline_utc": schedule.get("ScheduleExpressionTimezone") == "UTC",
        "deadline_pause_input": json.loads(schedule["Target"]["Input"]) == {"action": "pause", "source": "deadline"},
        "lambda_subscribed": any(item["Protocol"] == "lambda"
                                 and item["Endpoint"] == outputs["GuardFunctionArn"]
                                 and item["SubscriptionArn"].startswith("arn:") for item in subscriptions),
        "topic_budget_permission_scoped": allows_service(
            json.loads(topic["Policy"]), "budgets.amazonaws.com", "sns:Publish",
            outputs["StopTopicArn"], args.account, expected_budget_arn),
        "lambda_allows_stop_topic": allows_service(
            policy, "sns.amazonaws.com", "lambda:InvokeFunction", outputs["GuardFunctionArn"],
            args.account, outputs["StopTopicArn"]),
    }
    report = {"checked_at": datetime.now(UTC).isoformat(), "guard_status": stack["StackStatus"],
              "outputs": outputs, "parameters": params, "budget": budget,
              "notifications": subscribers, "schedule": schedule,
              "topic_attributes": topic, "subscriptions": subscriptions,
              "lambda_policy": policy, "dry_run": dry_run, "checks": checks}
    path = ROOT / "build/evidence/demo-guard-verify.json"
    write_json(path, report)
    if not all(checks.values()):
        raise ValueError(f"Guard verification failed: {[name for name, passed in checks.items() if not passed]}; report: {path}")
    return {"report": str(path), "guard_status": stack["StackStatus"],
            "deadline_utc": outputs["DeadlineUtc"], "checks": checks, "dry_run": dry_run}


def operate(session, args):
    _, _, outputs = guard_stack(session, args)
    action = args.command if args.apply else "dry_run"
    if args.command == "resume" and not args.apply:
        raise ValueError("Resume changes configuration; use resume --apply after reviewing dry_run")
    event = {"action": action, "source": "manual"}
    if args.override_deadline:
        event["override_deadline"] = True
    result = invoke_guard(session, outputs["GuardFunctionArn"], event)
    write_json(ROOT / f"build/evidence/demo-guard-{action}.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="marketdata-dev")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--account", default="972243443945")
    parser.add_argument("--target-stack", default="marketdata-hoang-dev")
    parser.add_argument("--guard-stack", default="marketdata-hoang-demo-guard")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare", help="Upload code and create a reviewable change set")
    prepare_parser.add_argument("--email", required=True)
    prepare_parser.add_argument("--artifact-bucket", required=True)
    commands.add_parser("apply", help="Execute the reviewed change set saved by prepare")
    commands.add_parser("verify", help="Read configuration and invoke dry_run only")
    for action in ("pause", "resume"):
        sub = commands.add_parser(action)
        sub.add_argument("--apply", action="store_true", help="Allow the configuration change")
        sub.add_argument("--override-deadline", action="store_true")
    args = parser.parse_args()
    try:
        session = checked_session(args)
        functions = {"prepare": prepare, "apply": apply_plan, "verify": verify,
                     "pause": operate, "resume": operate}
        result = functions[args.command](session, args)
        print(json.dumps(result, indent=2, default=str))
        return 0
    except (BotoCoreError, ClientError, ValueError, RuntimeError, OSError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
