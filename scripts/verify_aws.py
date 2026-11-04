"""Read-only inventory and configuration checks for the deployed dev stack."""

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

CONFIG = Config(connect_timeout=5, read_timeout=15,
                retries={"mode": "standard", "total_max_attempts": 3})


def new_session(profile, region):
    return boto3.Session(profile_name=profile or None, region_name=region)


def json_value(value):
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def write_report(report, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, default=json_value, indent=2) + "\n",
                    encoding="utf-8")


def get_stack(cfn, stack_name):
    stack = cfn.describe_stacks(StackName=stack_name)["Stacks"][0]
    resources = [item for page in cfn.get_paginator("list_stack_resources").paginate(StackName=stack_name)
                 for item in page.get("StackResourceSummaries", [])]
    return stack, resources


def inspect_stack(session, stack_name):
    cfn = session.client("cloudformation", config=CONFIG)
    stack, resources = get_stack(cfn, stack_name)
    identity = session.client("sts", config=CONFIG).get_caller_identity()
    report = {"mode": "read_only_live_aws", "checked_at": datetime.now(UTC).isoformat(),
              "stack_name": stack_name, "stack_id": stack["StackId"], "stack_status": stack["StackStatus"],
              "account_id": identity["Account"], "region": session.region_name,
              "resources": [{key: r.get(key) for key in
                             ("LogicalResourceId", "PhysicalResourceId", "ResourceType", "ResourceStatus")}
                            for r in resources], "checks": []}

    def check(name, passed, detail):
        report["checks"].append({"name": name, "passed": bool(passed), "detail": detail})

    check("stack_ready", stack["StackStatus"] in ("CREATE_COMPLETE", "UPDATE_COMPLETE"),
          stack["StackStatus"])
    params = {p["ParameterKey"]: p["ParameterValue"] for p in stack.get("Parameters", [])
              if p["ParameterKey"] in ("EnableCloudFront", "ScheduleState", "WebOrigin", "LocalDevOrigin")}
    report["parameters"] = params
    active = [r for r in resources if r["ResourceStatus"] not in ("DELETE_COMPLETE", "DELETE_SKIPPED")
              and r.get("PhysicalResourceId")]
    distributions = [r for r in active if r["ResourceType"] == "AWS::CloudFront::Distribution"]
    if params.get("EnableCloudFront") == "DISABLED":
        check("cloudfront_not_created", not distributions, "EnableCloudFront=DISABLED")
    if not report["checks"][0]["passed"]:
        report["status"] = "needs_attention"
        report["retained_resources"] = [r for r in report["resources"] if r["ResourceStatus"] == "DELETE_SKIPPED"]
        return report

    required = {"ControlTable", "ObservationsTable", "RawBucket", "ReportsBucket", "WebBucket",
                "CSVQueue", "APIQueue", "WebQueue", "TransformQueue", "ReportQueue",
                "CSVWorker", "APIWorker", "WebWorker", "Normalizer", "ReportAggregator",
                "JobDispatcher", "ReportDispatcher", "ApiFunction", "HttpApi", "UserPool",
                "NotificationFunction", "NotificationFunctionErrorsAlarm", "NotificationKey", "AlarmTopic"}
    required.update(name + "DLQAlarm" for name in
                    ("CSVQueue", "APIQueue", "WebQueue", "TransformQueue", "ReportQueue", "Dispatch"))
    missing = sorted(required - {r["LogicalResourceId"] for r in active})
    check("required_resources", not missing, {"missing": missing})

    s3, sqs = session.client("s3", config=CONFIG), session.client("sqs", config=CONFIG)
    lambdas, scheduler = session.client("lambda", config=CONFIG), session.client("scheduler", config=CONFIG)
    cloudwatch = session.client("cloudwatch", config=CONFIG)
    queues, functions, mappings = {}, {}, []
    schedule_groups = [r["PhysicalResourceId"] for r in active
                       if r["ResourceType"] == "AWS::Scheduler::ScheduleGroup"]
    for resource in active:
        kind, logical, physical = (resource[k] for k in
                                   ("ResourceType", "LogicalResourceId", "PhysicalResourceId"))
        if kind == "AWS::S3::Bucket":
            block = s3.get_public_access_block(Bucket=physical)["PublicAccessBlockConfiguration"]
            rules = s3.get_bucket_encryption(Bucket=physical)["ServerSideEncryptionConfiguration"]["Rules"]
            check(logical + ".private", all(block.values()), "All public access blocks enabled")
            check(logical + ".encrypted", bool(rules), "Default bucket encryption configured")
            if logical == "RawBucket":
                versioning = s3.get_bucket_versioning(Bucket=physical).get("Status")
                check(logical + ".versioned", versioning == "Enabled", versioning)
        elif kind == "AWS::SQS::Queue":
            attrs = sqs.get_queue_attributes(QueueUrl=physical, AttributeNames=["All"])["Attributes"]
            queues[attrs["QueueArn"]] = attrs
            check(logical + ".encrypted", attrs.get("SqsManagedSseEnabled") == "true" or bool(attrs.get("KmsMasterKeyId")),
                  "SQS-managed or KMS encryption configured")
            if "RedrivePolicy" in attrs:
                policy = json.loads(attrs["RedrivePolicy"])
                check(logical + ".redrive", int(policy["maxReceiveCount"]) == 5,
                      {"max_receive_count": policy["maxReceiveCount"], "dlq_arn": policy["deadLetterTargetArn"]})
        elif kind == "AWS::Lambda::Function":
            cfg = lambdas.get_function_configuration(FunctionName=physical)
            functions[cfg["FunctionArn"]] = cfg
            if logical == "ApiFunction":
                test_auth = cfg.get("Environment", {}).get("Variables", {}).get("ALLOW_TEST_AUTH", "false")
                check("api_test_auth_disabled", test_auth.lower() != "true", "No test headers in live API")
            elif logical == "NotificationFunction":
                env = cfg.get("Environment", {}).get("Variables", {})
                guarded = (env.get("NOTIFICATION_STACK_NAME") == stack_name
                           and env.get("AWS_ACCOUNT_ID") == identity["Account"])
                check("notification_tenant_guard_configured", guarded,
                      "Destination validation requires this stack and account")
                check("notification_no_global_channel", not any(
                    env.get(key) for key in ("SNS_TOPIC_ARN", "SLACK_WEBHOOK_URL")),
                    "Notification channels come from each tenant's metadata")
        elif kind == "AWS::Lambda::EventSourceMapping":
            mappings.append((logical, lambdas.get_event_source_mapping(UUID=physical)))
        elif kind == "AWS::Scheduler::Schedule":
            group, name = physical.split("/", 1) if "/" in physical else (
                schedule_groups[0] if len(schedule_groups) == 1 else "default", physical)
            cfg = scheduler.get_schedule(Name=name, GroupName=group)
            check(logical + ".disabled", cfg["State"] == "DISABLED", cfg["State"])
        elif kind == "AWS::CloudWatch::Alarm":
            alarms = cloudwatch.describe_alarms(AlarmNames=[physical]).get("MetricAlarms", [])
            check(logical + ".actions", bool(alarms and alarms[0].get("AlarmActions")),
                  "Configured actions; email delivery still requires subscription confirmation")

    for logical, mapping in mappings:
        queue, function = queues.get(mapping["EventSourceArn"]), functions.get(mapping["FunctionArn"])
        check(logical + ".enabled", mapping["State"] == "Enabled", mapping["State"])
        check(logical + ".partial_batch", "ReportBatchItemFailures" in mapping.get("FunctionResponseTypes", []),
              mapping.get("FunctionResponseTypes", []))
        concurrency = mapping.get("ScalingConfig", {}).get("MaximumConcurrency")
        check(logical + ".concurrency", concurrency == 2, {"actual": concurrency, "expected": 2})
        if queue and function:
            required = 6 * function["Timeout"] + mapping.get("MaximumBatchingWindowInSeconds", 0)
            check(logical + ".visibility", int(queue["VisibilityTimeout"]) >= required,
                  {"actual": int(queue["VisibilityTimeout"]), "minimum": required})
        else:
            check(logical + ".target_found", False, "Queue/function not in the stack inventory")
    check("sqs_consumer_count", len(mappings) >= 5, {"actual": len(mappings), "minimum": 5})
    report["not_tested"] = ["IAM runtime authorization", "Cognito login", "API tenant isolation",
                            "CSV processing", "SNS email delivery", "Slack delivery", "DLQ redrive"]
    report["status"] = "passed" if all(c["passed"] for c in report["checks"]) else "needs_attention"
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.getenv("AWS_PROFILE", "marketdata-dev"))
    parser.add_argument("--region", default=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
    parser.add_argument("--stack", default="marketdata-hoang-dev")
    parser.add_argument("--output", type=Path, default=Path("build/evidence/aws-preflight.json"))
    args = parser.parse_args(argv)
    try:
        report = inspect_stack(new_session(args.profile, args.region), args.stack)
    except (BotoCoreError, ClientError, RuntimeError, KeyError, ValueError) as exc:
        report = {"status": "failed", "mode": "read_only_live_aws", "stack_name": args.stack,
                  "region": args.region, "checked_at": datetime.now(UTC).isoformat(),
                  "error": {"type": type(exc).__name__, "code": (
                      exc.response["Error"]["Code"] if isinstance(exc, ClientError) else str(exc))}}
    write_report(report, args.output)
    print(json.dumps(report, ensure_ascii=False, default=json_value, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
