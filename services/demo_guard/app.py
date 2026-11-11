"""Pause one demo stack while retaining its data and original runtime settings."""

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

STATE_KEY = "pause-state.json"
LOCK_KEY = "guard-lock.json"
RESOURCE_TYPES = {
    "AWS::Lambda::Function",
    "AWS::Lambda::EventSourceMapping",
    "AWS::Scheduler::Schedule",
    "AWS::Events::Rule",
}
SCHEDULE_FIELDS = {
    "ActionAfterCompletion",
    "Description",
    "EndDate",
    "FlexibleTimeWindow",
    "GroupName",
    "KmsKeyArn",
    "Name",
    "ScheduleExpression",
    "ScheduleExpressionTimezone",
    "StartDate",
    "State",
    "Target",
}


@dataclass(frozen=True)
class Settings:
    stack_id: str
    account_id: str
    bucket: str
    stop_topic_arn: str
    budget_name: str
    deadline: datetime

    @property
    def region(self):
        return self.stack_id.split(":")[3]

    def validate(self):
        match = re.fullmatch(
            r"arn:aws:cloudformation:([a-z0-9-]+):(\d{12}):stack/([A-Za-z0-9-]+)/[A-Za-z0-9-]+",
            self.stack_id,
        )
        if not match or match[2] != self.account_id:
            raise ValueError("TARGET_STACK_ID must be an exact stack ARN in EXPECTED_ACCOUNT_ID")
        if not self.bucket or not self.budget_name or self.deadline.tzinfo is None:
            raise ValueError("Missing state bucket, budget name or timezone-aware deadline")
        if not self.stop_topic_arn.startswith(f"arn:aws:sns:{self.region}:{self.account_id}:"):
            raise ValueError("STOP_TOPIC_ARN must belong to the target account and region")


def settings_from_env():
    return Settings(
        stack_id=os.environ["TARGET_STACK_ID"],
        account_id=os.environ["EXPECTED_ACCOUNT_ID"],
        bucket=os.environ["STATE_BUCKET"],
        stop_topic_arn=os.environ["STOP_TOPIC_ARN"],
        budget_name=os.environ["BUDGET_NAME"],
        deadline=datetime.fromisoformat(os.environ["DEADLINE_UTC"].replace("Z", "+00:00")),
    )


@lru_cache(maxsize=1)
def aws_clients(region):
    config = Config(
        connect_timeout=5,
        read_timeout=10,
        retries={"total_max_attempts": 3, "mode": "standard"},
    )
    return {
        name: boto3.client(name, region_name=region, config=config)
        for name in ("cloudformation", "lambda", "scheduler", "events", "s3")
    }


def _action(event, settings, now):
    if not isinstance(event, dict):
        raise ValueError("Event must be an object")
    if "Records" in event:
        records = event["Records"]
        if not records or any(
            record.get("EventSource") != "aws:sns"
            or record.get("Sns", {}).get("TopicArn") != settings.stop_topic_arn
            for record in records
        ):
            raise ValueError("Untrusted SNS topic")
        # Budget messages are informational; they can never select a stack or resume it.
        return "pause"
    action, source = event.get("action"), event.get("source")
    if source == "deadline" and action == "pause":
        if now < settings.deadline:
            raise ValueError("Deadline has not elapsed")
        return action
    if source != "manual" or action not in {"pause", "resume", "dry_run"}:
        raise ValueError("Expected a manual action or an elapsed deadline")
    if (
        action == "resume"
        and now >= settings.deadline
        and event.get("override_deadline") is not True
    ):
        raise ValueError("Resume after the deadline requires override_deadline=true")
    return action


def _schedule_location(physical_id, settings, default_group):
    prefix = f"arn:aws:scheduler:{settings.region}:{settings.account_id}:schedule/"
    if physical_id.startswith("arn:"):
        if not physical_id.startswith(prefix):
            raise ValueError("Schedule ARN is outside the target account or region")
        group, name = physical_id[len(prefix) :].split("/", 1)
    elif "|" in physical_id:
        group, name = physical_id.split("|", 1)
    else:
        group, name = default_group, physical_id
    if not all(re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", value) for value in (group, name)):
        raise ValueError("Invalid schedule resource ID")
    return {"GroupName": group, "Name": name}


def _targets(clients, settings):
    targets = []
    paginator = clients["cloudformation"].get_paginator("list_stack_resources")
    resources = [
        resource
        for page in paginator.paginate(StackName=settings.stack_id)
        for resource in page["StackResourceSummaries"]
    ]
    groups = {
        resource["PhysicalResourceId"]
        for resource in resources
        if resource["ResourceType"] == "AWS::Scheduler::ScheduleGroup"
        and resource.get("PhysicalResourceId")
    }
    if len(groups) > 1:
        raise ValueError("Multiple schedule groups need explicit schedule resource IDs")
    default_group = next(iter(groups), "default")
    for resource in resources:
        resource_type = resource["ResourceType"]
        if resource_type not in RESOURCE_TYPES:
            continue
        physical_id = resource.get("PhysicalResourceId")
        if not physical_id or resource.get("ResourceStatus", "").startswith("DELETE_"):
            raise ValueError("Target resource is missing or being deleted")
        target = {
            "logical_id": resource["LogicalResourceId"],
            "type": resource_type,
            "id": physical_id,
        }
        if resource_type == "AWS::Lambda::Function":
            prefix = f"arn:aws:lambda:{settings.region}:{settings.account_id}:function:"
            name = physical_id.removeprefix(prefix)
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
                raise ValueError("Invalid Lambda resource ID")
        elif resource_type == "AWS::Lambda::EventSourceMapping":
            if not re.fullmatch(r"[a-fA-F0-9-]{36}", physical_id):
                raise ValueError("Invalid event source mapping ID")
        elif resource_type == "AWS::Scheduler::Schedule":
            target["location"] = _schedule_location(physical_id, settings, default_group)
        elif not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", physical_id):
            raise ValueError("Only default-bus EventBridge rules are supported")
        targets.append(target)
    if not targets:
        raise ValueError("No supported runtime resources found in the target stack")
    return sorted(targets, key=lambda target: (target["type"], target["logical_id"]))


def _schedule(clients, target, settings):
    result = clients["scheduler"].get_schedule(**target["location"])
    location = target["location"]
    expected = (
        f"arn:aws:scheduler:{settings.region}:{settings.account_id}:schedule/"
        f"{location['GroupName']}/{location['Name']}"
    )
    if result["Arn"] != expected:
        raise ValueError("Scheduler returned a resource outside the target scope")
    return {key: value for key, value in result.items() if key in SCHEDULE_FIELDS}


def _capture(clients, settings, targets, now):
    captured = []
    function_ids = {
        target["id"].rsplit(":", 1)[-1]
        for target in targets
        if target["type"] == "AWS::Lambda::Function"
    }
    for target in targets:
        original = {}
        resource_type, physical_id = target["type"], target["id"]
        if resource_type == "AWS::Lambda::Function":
            original["concurrency"] = (
                clients["lambda"]
                .get_function_concurrency(
                    FunctionName=physical_id,
                )
                .get("ReservedConcurrentExecutions")
            )
        elif resource_type == "AWS::Lambda::EventSourceMapping":
            mapping = clients["lambda"].get_event_source_mapping(UUID=physical_id)
            prefix = f"arn:aws:lambda:{settings.region}:{settings.account_id}:function:"
            function_arn = mapping["FunctionArn"]
            if (
                not function_arn.startswith(prefix)
                or function_arn[len(prefix) :].split(":")[0] not in function_ids
            ):
                raise ValueError("Mapping references a function outside the target stack")
            original["state"] = mapping["State"]
        elif resource_type == "AWS::Scheduler::Schedule":
            original["state"] = _schedule(clients, target, settings)["State"]
        else:
            rule = clients["events"].describe_rule(Name=physical_id, EventBusName="default")
            expected = f"arn:aws:events:{settings.region}:{settings.account_id}:rule/{physical_id}"
            if rule["Arn"] != expected:
                raise ValueError("Rule is outside the target stack account or region")
            original["state"] = rule["State"]
        if "state" in original and original["state"] not in {
            "Enabled",
            "Disabled",
            "ENABLED",
            "DISABLED",
        }:
            raise ValueError(
                f"Resource is in a transitional or unsupported state: {target['logical_id']}"
            )
        captured.append({**target, "original": original})
    return {
        "schema_version": 1,
        "stack_id": settings.stack_id,
        "created_at": now.isoformat(),
        "targets": captured,
    }


def _load_snapshot(clients, settings):
    try:
        response = clients["s3"].get_object(Bucket=settings.bucket, Key=STATE_KEY)
    except clients["s3"].exceptions.NoSuchKey:
        return None
    with response["Body"] as body:
        return json.loads(body.read())


def _validate_snapshot(snapshot, targets, settings):
    if snapshot.get("schema_version") != 1 or snapshot.get("stack_id") != settings.stack_id:
        raise ValueError("Snapshot belongs to a different stack or schema")

    def identities(values):
        return {(target["logical_id"], target["type"], target["id"]) for target in values}

    if identities(snapshot["targets"]) != identities(targets):
        raise ValueError("Stack resources changed since pause; review the saved snapshot")
    for saved in snapshot["targets"]:
        current = next(target for target in targets if target["logical_id"] == saved["logical_id"])
        if saved.get("location") != current.get("location"):
            raise ValueError("Snapshot schedule location does not match CloudFormation")
        original = saved["original"]
        if saved["type"] == "AWS::Lambda::Function":
            value = original["concurrency"]
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("Invalid concurrency in snapshot")
        elif original.get("state") not in {"Enabled", "Disabled", "ENABLED", "DISABLED"}:
            raise ValueError("Invalid resource state in snapshot")


def _save_snapshot(clients, settings, snapshot, targets):
    try:
        clients["s3"].put_object(
            Bucket=settings.bucket,
            Key=STATE_KEY,
            Body=json.dumps(snapshot).encode("utf-8"),
            ContentType="application/json",
            ServerSideEncryption="AES256",
            IfNoneMatch="*",
        )
        return snapshot
    except ClientError as error:
        # A concurrent invocation saved the originals first; never replace them.
        if error.response["Error"]["Code"] not in {
            "PreconditionFailed",
            "ConditionalRequestConflict",
        }:
            raise
        existing = _load_snapshot(clients, settings)
        if existing is None:
            raise RuntimeError(
                "Snapshot write conflicted; retry before changing any resources"
            ) from error
        _validate_snapshot(existing, targets, settings)
        return existing


def _acquire_lock(clients, settings, now):
    # The lease outlives the function's 120-second timeout. S3 conditionals serialize callers.
    body = json.dumps({"owner": str(uuid4()), "expires_at": now.timestamp() + 150}).encode()

    def create():
        return clients["s3"].put_object(
            Bucket=settings.bucket,
            Key=LOCK_KEY,
            Body=body,
            ContentType="application/json",
            ServerSideEncryption="AES256",
            IfNoneMatch="*",
        )["ETag"]

    try:
        return create()
    except ClientError as error:
        if error.response["Error"]["Code"] not in {
            "PreconditionFailed",
            "ConditionalRequestConflict",
        }:
            raise
    try:
        existing = clients["s3"].get_object(Bucket=settings.bucket, Key=LOCK_KEY)
    except clients["s3"].exceptions.NoSuchKey:
        existing = None
    if existing is not None:
        with existing["Body"] as stream:
            lock = json.loads(stream.read())
        expiry = lock["expires_at"]
        if type(expiry) not in (int, float) or expiry > now.timestamp():
            raise RuntimeError("Another guard operation holds the lock; retry later")
        # If another caller has replaced this lease, its ETag prevents deleting its lock.
        try:
            clients["s3"].delete_object(
                Bucket=settings.bucket,
                Key=LOCK_KEY,
                IfMatch=existing["ETag"],
            )
        except ClientError as error:
            if error.response["Error"]["Code"] not in {
                "PreconditionFailed",
                "ConditionalRequestConflict",
            }:
                raise
            raise RuntimeError("Guard lock changed; retry later") from error
    try:
        return create()
    except ClientError as error:
        if error.response["Error"]["Code"] not in {
            "PreconditionFailed",
            "ConditionalRequestConflict",
        }:
            raise
        raise RuntimeError("Another guard operation acquired the lock; retry later") from error


def _release_lock(clients, settings, etag):
    clients["s3"].delete_object(Bucket=settings.bucket, Key=LOCK_KEY, IfMatch=etag)


def _set_state(clients, settings, target, resume):
    resource_type, physical_id = target["type"], target["id"]
    original = target["original"]
    if resource_type == "AWS::Lambda::Function":
        concurrency = original["concurrency"] if resume else 0
        if concurrency is None:
            clients["lambda"].delete_function_concurrency(FunctionName=physical_id)
        else:
            clients["lambda"].put_function_concurrency(
                FunctionName=physical_id,
                ReservedConcurrentExecutions=concurrency,
            )
    elif resource_type == "AWS::Lambda::EventSourceMapping":
        clients["lambda"].update_event_source_mapping(
            UUID=physical_id,
            Enabled=resume and original["state"] == "Enabled",
        )
    elif resource_type == "AWS::Scheduler::Schedule":
        schedule = _schedule(clients, target, settings)
        schedule["State"] = original["state"] if resume else "DISABLED"
        clients["scheduler"].update_schedule(**schedule)
    else:
        enabled = resume and original["state"] == "ENABLED"
        if enabled:
            clients["events"].enable_rule(Name=physical_id, EventBusName="default")
        else:
            clients["events"].disable_rule(Name=physical_id, EventBusName="default")


def run(event, clients, settings, now):
    settings.validate()
    action = _action(event, settings, now)
    if action == "dry_run":
        targets = _targets(clients, settings)
        return {"action": action, "snapshot": _capture(clients, settings, targets, now)}
    etag = _acquire_lock(clients, settings, now)
    try:
        return _mutate(action, clients, settings, now)
    finally:
        _release_lock(clients, settings, etag)


def _mutate(action, clients, settings, now):
    targets = _targets(clients, settings)
    snapshot = _load_snapshot(clients, settings)
    if snapshot is not None:
        _validate_snapshot(snapshot, targets, settings)
    elif action == "resume":
        return {"action": action, "status": "already_resumed", "stack_id": settings.stack_id}
    else:
        snapshot = _capture(clients, settings, targets, now)
        snapshot = _save_snapshot(clients, settings, snapshot, targets)
    failures = []
    # Stop incoming work before workers. Resume workers before their triggers.
    order = {
        "AWS::Scheduler::Schedule": 0,
        "AWS::Events::Rule": 1,
        "AWS::Lambda::EventSourceMapping": 2,
        "AWS::Lambda::Function": 3,
    }
    previous_type = None
    for target in sorted(
        snapshot["targets"], key=lambda item: order[item["type"]], reverse=action == "resume"
    ):
        if action == "resume" and failures and target["type"] != previous_type:
            # Leave upstream triggers paused when a downstream component cannot resume.
            break
        previous_type = target["type"]
        try:
            _set_state(clients, settings, target, resume=action == "resume")
        except Exception as error:
            # Stop the remaining resources even when one service rejects an update.
            logger.error(
                "guard_%s_failed logical_id=%s error_type=%s",
                action,
                target["logical_id"],
                type(error).__name__,
            )
            failures.append(target["logical_id"])
    if failures:
        raise RuntimeError(f"{action} incomplete; retry failed resources: {', '.join(failures)}")
    if action == "resume":
        # The state bucket is versioned: its prior version remains available for review.
        clients["s3"].delete_object(Bucket=settings.bucket, Key=STATE_KEY)
    return {
        "action": action,
        "status": "complete",
        "stack_id": settings.stack_id,
        "resource_count": len(targets),
        "budget_name": settings.budget_name,
    }


def handler(event, context):
    settings = settings_from_env()
    settings.validate()
    if context is not None:
        invoked = context.invoked_function_arn.split(":")
        if invoked[3:5] != [settings.region, settings.account_id]:
            raise ValueError("Guard function is outside the expected account or region")
    return run(event, aws_clients(settings.region), settings, datetime.now(UTC))
