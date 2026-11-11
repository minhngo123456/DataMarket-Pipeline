import io
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

from services.demo_guard.app import LOCK_KEY, STATE_KEY, Settings, run

ACCOUNT = "972243443945"
REGION = "us-east-1"
STACK_ID = f"arn:aws:cloudformation:{REGION}:{ACCOUNT}:stack/marketdata-hoang-dev/1234-abcd"
NOW = datetime(2026, 11, 5, 0, 30, tzinfo=UTC)
SETTINGS = Settings(
    STACK_ID,
    ACCOUNT,
    "guard-state",
    f"arn:aws:sns:{REGION}:{ACCOUNT}:demo-stop",
    "demo-budget",
    NOW + timedelta(days=7),
)
FUNCTION_PREFIX = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:"
MAPPING_A = "12345678-1111-2222-3333-123456789abc"
MAPPING_B = "12345678-1111-2222-3333-123456789abd"


class NoSuchKey(Exception):
    pass


def resource(logical_id, resource_type, physical_id):
    return {
        "LogicalResourceId": logical_id,
        "ResourceType": resource_type,
        "PhysicalResourceId": physical_id,
        "ResourceStatus": "CREATE_COMPLETE",
    }


class FakeAws:
    def __init__(self):
        self.exceptions = SimpleNamespace(NoSuchKey=NoSuchKey)
        self.resources = [
            resource("FunctionA", "AWS::Lambda::Function", "app-a"),
            resource("FunctionB", "AWS::Lambda::Function", "app-b"),
            resource("FunctionOff", "AWS::Lambda::Function", "app-off"),
            resource("MappingA", "AWS::Lambda::EventSourceMapping", MAPPING_A),
            resource("MappingB", "AWS::Lambda::EventSourceMapping", MAPPING_B),
            resource("SchedulerGroup", "AWS::Scheduler::ScheduleGroup", "demo-group"),
            resource("ScheduleA", "AWS::Scheduler::Schedule", "schedule-a"),
            resource("ScheduleB", "AWS::Scheduler::Schedule", "schedule-b"),
            resource("RuleA", "AWS::Events::Rule", "rule-a"),
            resource("RuleB", "AWS::Events::Rule", "rule-b"),
            resource("RawBucket", "AWS::S3::Bucket", "application-data"),
        ]
        self.concurrency = {"app-a": None, "app-b": 2, "app-off": 0}
        self.mappings = {
            MAPPING_A: {"State": "Enabled", "FunctionArn": FUNCTION_PREFIX + "app-a"},
            MAPPING_B: {"State": "Disabled", "FunctionArn": FUNCTION_PREFIX + "app-b"},
        }
        self.schedules = {
            name: {
                "Arn": f"arn:aws:scheduler:{REGION}:{ACCOUNT}:schedule/demo-group/{name}",
                "Name": name,
                "GroupName": "demo-group",
                "State": state,
                "ScheduleExpression": "rate(6 hours)",
                "FlexibleTimeWindow": {"Mode": "OFF"},
                "Target": {"Arn": FUNCTION_PREFIX + "app-a", "RoleArn": "role-arn"},
                "CreationDate": NOW,
            }
            for name, state in (("schedule-a", "ENABLED"), ("schedule-b", "DISABLED"))
        }
        self.rules = {
            name: {"Arn": f"arn:aws:events:{REGION}:{ACCOUNT}:rule/{name}", "State": state}
            for name, state in (("rule-a", "ENABLED"), ("rule-b", "DISABLED"))
        }
        self.snapshot = None
        self.versions = []
        self.calls = []
        self.fail_function = None
        self.lock = None
        self.lock_etag = None
        self.lock_count = 0

    @property
    def clients(self):
        return {name: self for name in ("cloudformation", "lambda", "scheduler", "events", "s3")}

    @property
    def writes(self):
        return [
            call
            for call in self.calls
            if call[0]
            in {
                "put_object",
                "delete_object",
                "put_function_concurrency",
                "delete_function_concurrency",
                "update_event_source_mapping",
                "update_schedule",
                "enable_rule",
                "disable_rule",
            }
        ]

    @property
    def target_writes(self):
        return [call for call in self.writes if call[1].get("Key") != LOCK_KEY]

    def get_paginator(self, name):
        assert name == "list_stack_resources"
        return self

    def paginate(self, **kwargs):
        self.calls.append(("list_stack_resources", kwargs))
        assert kwargs == {"StackName": STACK_ID}
        # Pagination must include the schedule group even when it is on another page.
        return [
            {"StackResourceSummaries": self.resources[:5]},
            {"StackResourceSummaries": self.resources[5:]},
        ]

    def get_function_concurrency(self, FunctionName):
        value = self.concurrency[FunctionName]
        return {} if value is None else {"ReservedConcurrentExecutions": value}

    def get_event_source_mapping(self, UUID):
        return deepcopy(self.mappings[UUID])

    def get_schedule(self, Name, GroupName):
        assert GroupName == "demo-group"
        return deepcopy(self.schedules[Name])

    def describe_rule(self, Name, EventBusName):
        assert EventBusName == "default"
        return deepcopy(self.rules[Name])

    def get_object(self, **kwargs):
        assert kwargs["Bucket"] == "guard-state"
        if kwargs["Key"] == LOCK_KEY:
            if self.lock is None:
                raise NoSuchKey()
            return {"Body": io.BytesIO(self.lock), "ETag": self.lock_etag}
        assert kwargs["Key"] == STATE_KEY
        if self.snapshot is None:
            raise NoSuchKey()
        return {"Body": io.BytesIO(self.snapshot)}

    def put_object(self, **kwargs):
        self.calls.append(("put_object", kwargs))
        assert kwargs["Bucket"] == "guard-state"
        assert kwargs["IfNoneMatch"] == "*" and kwargs["ServerSideEncryption"] == "AES256"
        if kwargs["Key"] == LOCK_KEY:
            if self.lock is not None:
                raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
            self.lock_count += 1
            self.lock = kwargs["Body"]
            self.lock_etag = f'"lock-{self.lock_count}"'
            return {"ETag": self.lock_etag}
        assert kwargs["Key"] == STATE_KEY
        if self.snapshot is not None:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.snapshot = kwargs["Body"]
        self.versions.append(self.snapshot)

    def delete_object(self, **kwargs):
        self.calls.append(("delete_object", kwargs))
        if kwargs["Key"] == LOCK_KEY:
            assert kwargs["Bucket"] == "guard-state"
            if kwargs["IfMatch"] != self.lock_etag:
                raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "DeleteObject")
            self.lock = None
            self.lock_etag = None
            return
        assert kwargs == {"Bucket": "guard-state", "Key": STATE_KEY}
        self.snapshot = None

    def put_function_concurrency(self, **kwargs):
        self.calls.append(("put_function_concurrency", kwargs))
        if kwargs["FunctionName"] == self.fail_function:
            raise RuntimeError("temporarily unavailable")
        self.concurrency[kwargs["FunctionName"]] = kwargs["ReservedConcurrentExecutions"]

    def delete_function_concurrency(self, **kwargs):
        self.calls.append(("delete_function_concurrency", kwargs))
        self.concurrency[kwargs["FunctionName"]] = None

    def update_event_source_mapping(self, **kwargs):
        self.calls.append(("update_event_source_mapping", kwargs))
        self.mappings[kwargs["UUID"]]["State"] = "Enabled" if kwargs["Enabled"] else "Disabled"

    def update_schedule(self, **kwargs):
        self.calls.append(("update_schedule", kwargs))
        assert "CreationDate" not in kwargs and "Arn" not in kwargs
        self.schedules[kwargs["Name"]].update(deepcopy(kwargs))

    def enable_rule(self, **kwargs):
        self.calls.append(("enable_rule", kwargs))
        self.rules[kwargs["Name"]]["State"] = "ENABLED"

    def disable_rule(self, **kwargs):
        self.calls.append(("disable_rule", kwargs))
        self.rules[kwargs["Name"]]["State"] = "DISABLED"


def execute(aws, action="pause", now=NOW, **event):
    return run({"action": action, "source": "manual", **event}, aws.clients, SETTINGS, now)


def test_dry_run_is_read_only_and_scoped_to_exact_stack():
    aws = FakeAws()
    result = execute(aws, "dry_run")
    assert result["action"] == "dry_run"
    assert len(result["snapshot"]["targets"]) == 9
    assert not aws.writes
    assert result["snapshot"]["stack_id"] == STACK_ID
    assert all(target["id"] != "application-data" for target in result["snapshot"]["targets"])


def test_pause_saves_originals_before_mutation_and_duplicate_keeps_snapshot():
    aws = FakeAws()
    execute(aws)
    original_snapshot = aws.snapshot
    execute(aws)
    assert aws.target_writes[0][0] == "put_object"
    assert aws.snapshot == original_snapshot
    assert sum(call[0] == "put_object" for call in aws.target_writes) == 1
    assert aws.concurrency == {"app-a": 0, "app-b": 0, "app-off": 0}
    assert all(value["State"] == "Disabled" for value in aws.mappings.values())
    assert all(value["State"] == "DISABLED" for value in aws.schedules.values())
    assert all(value["State"] == "DISABLED" for value in aws.rules.values())


def test_partial_pause_still_stops_other_resources_and_can_retry():
    aws = FakeAws()
    aws.fail_function = "app-a"
    with pytest.raises(RuntimeError, match="FunctionA"):
        execute(aws)
    snapshot = aws.snapshot
    assert aws.concurrency["app-b"] == 0
    assert all(value["State"] == "DISABLED" for value in aws.schedules.values())
    assert all(value["State"] == "Disabled" for value in aws.mappings.values())
    aws.fail_function = None
    execute(aws)
    assert aws.snapshot == snapshot
    assert aws.concurrency["app-a"] == 0


def test_resume_restores_existing_disabled_states_and_original_concurrency():
    aws = FakeAws()
    execute(aws)
    execute(aws, "resume")
    assert aws.concurrency == {"app-a": None, "app-b": 2, "app-off": 0}
    assert aws.mappings[MAPPING_A]["State"] == "Enabled"
    assert aws.mappings[MAPPING_B]["State"] == "Disabled"
    assert aws.schedules["schedule-a"]["State"] == "ENABLED"
    assert aws.schedules["schedule-b"]["State"] == "DISABLED"
    assert aws.rules["rule-a"]["State"] == "ENABLED"
    assert aws.rules["rule-b"]["State"] == "DISABLED"
    assert aws.snapshot is None and len(aws.versions) == 1
    assert execute(aws, "resume")["status"] == "already_resumed"
    execute(aws)
    assert len(aws.versions) == 2
    assert json.loads(aws.versions[-1])["targets"] == json.loads(aws.versions[0])["targets"]


def test_failed_resume_keeps_snapshot_for_retry():
    aws = FakeAws()
    execute(aws)
    aws.fail_function = "app-b"
    with pytest.raises(RuntimeError, match="FunctionB"):
        execute(aws, "resume")
    assert aws.snapshot is not None
    assert all(value["State"] == "Disabled" for value in aws.mappings.values())
    assert all(value["State"] == "DISABLED" for value in aws.schedules.values())
    assert all(value["State"] == "DISABLED" for value in aws.rules.values())
    aws.fail_function = None
    execute(aws, "resume")
    assert aws.concurrency["app-b"] == 2 and aws.snapshot is None


def test_sns_only_trusts_stop_topic_and_ignores_message_instructions():
    aws = FakeAws()
    event = {
        "Records": [
            {
                "EventSource": "aws:sns",
                "Sns": {
                    "TopicArn": SETTINGS.stop_topic_arn,
                    "Message": '{"action":"resume","stack":"other"}',
                },
            }
        ]
    }
    result = run(event, aws.clients, SETTINGS, NOW)
    assert result["action"] == "pause"
    event["Records"][0]["Sns"]["TopicArn"] += "-warning"
    aws.calls.clear()
    with pytest.raises(ValueError, match="Untrusted SNS"):
        run(event, aws.clients, SETTINGS, NOW)
    assert not aws.calls


def test_deadline_must_elapse_and_resume_requires_explicit_override():
    aws = FakeAws()
    with pytest.raises(ValueError, match="not elapsed"):
        execute(aws, source="deadline")
    assert not aws.calls
    execute(aws, source="deadline", now=SETTINGS.deadline)
    with pytest.raises(ValueError, match="override_deadline"):
        execute(aws, "resume", now=SETTINGS.deadline)
    execute(aws, "resume", now=SETTINGS.deadline, override_deadline=True)
    assert aws.snapshot is None


@pytest.mark.parametrize(
    "change", ["missing_id", "outside_mapping", "outside_rule", "transitional_mapping"]
)
def test_capture_failure_never_mutates_any_resource(change):
    aws = FakeAws()
    if change == "missing_id":
        aws.resources[0].pop("PhysicalResourceId")
    elif change == "outside_mapping":
        aws.mappings[MAPPING_A]["FunctionArn"] = FUNCTION_PREFIX + "another-stack-worker"
    elif change == "outside_rule":
        aws.rules["rule-a"]["Arn"] = "arn:aws:events:us-west-2:000000000000:rule/rule-a"
    else:
        aws.mappings[MAPPING_A]["State"] = "Enabling"
    with pytest.raises(ValueError):
        execute(aws)
    assert not aws.target_writes


def test_changed_stack_inventory_is_rejected_before_retry_mutates():
    aws = FakeAws()
    execute(aws)
    aws.calls.clear()
    aws.resources[0]["PhysicalResourceId"] = "replacement-worker"
    with pytest.raises(ValueError, match="resources changed"):
        execute(aws)
    assert not aws.target_writes


def test_snapshot_creation_race_uses_first_saved_originals():
    aws = FakeAws()
    execute(aws)
    originals = aws.snapshot
    real_get = aws.get_object
    missed_once = False

    def get_with_initial_miss(**kwargs):
        nonlocal missed_once
        if kwargs["Key"] == STATE_KEY and not missed_once:
            missed_once = True
            raise NoSuchKey()
        return real_get(**kwargs)

    aws.get_object = get_with_initial_miss
    execute(aws)
    assert aws.snapshot == originals
    execute(aws, "resume")
    assert aws.concurrency["app-b"] == 2


def test_snapshot_for_other_stack_is_rejected():
    aws = FakeAws()
    execute(aws)
    value = json.loads(aws.snapshot)
    value["stack_id"] = STACK_ID.replace("marketdata-hoang-dev", "another-stack")
    aws.snapshot = json.dumps(value).encode()
    aws.calls.clear()
    with pytest.raises(ValueError, match="different stack"):
        execute(aws, "resume")
    assert not aws.target_writes


def test_busy_lock_rejects_changes_and_does_not_release_other_owner():
    aws = FakeAws()
    aws.lock = json.dumps({"owner": "other", "expires_at": NOW.timestamp() + 100}).encode()
    aws.lock_etag = '"other-lock"'
    with pytest.raises(RuntimeError, match="holds the lock"):
        execute(aws)
    assert not aws.target_writes
    assert aws.lock_etag == '"other-lock"'
    assert all(call[0] != "delete_object" for call in aws.calls)


def test_stale_lock_is_reclaimed_conditionally_and_released():
    aws = FakeAws()
    aws.lock = json.dumps({"owner": "old", "expires_at": NOW.timestamp() - 1}).encode()
    aws.lock_etag = '"old-lock"'
    execute(aws)
    deletes = [call[1] for call in aws.calls if call[0] == "delete_object"]
    assert deletes[0]["IfMatch"] == '"old-lock"'
    assert deletes[-1]["IfMatch"] == '"lock-1"'
    assert aws.lock is None


def test_lock_is_released_after_failed_pause():
    aws = FakeAws()
    aws.fail_function = "app-a"
    with pytest.raises(RuntimeError, match="incomplete"):
        execute(aws)
    assert aws.snapshot is not None
    assert aws.lock is None


def test_stale_lock_race_does_not_delete_new_owners_lock():
    aws = FakeAws()
    aws.lock = json.dumps({"owner": "old", "expires_at": NOW.timestamp() - 1}).encode()
    aws.lock_etag = '"old-lock"'
    real_get = aws.get_object

    def replace_after_read(**kwargs):
        response = real_get(**kwargs)
        if kwargs["Key"] == LOCK_KEY:
            aws.lock_etag = '"new-owner-lock"'
        return response

    aws.get_object = replace_after_read
    with pytest.raises(RuntimeError, match="lock changed"):
        execute(aws)
    assert not aws.target_writes
    assert aws.lock_etag == '"new-owner-lock"'


def test_failed_snapshot_write_never_changes_runtime_and_releases_lock():
    aws = FakeAws()
    real_put = aws.put_object

    def deny_snapshot(**kwargs):
        if kwargs["Key"] == STATE_KEY:
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")
        return real_put(**kwargs)

    aws.put_object = deny_snapshot
    with pytest.raises(ClientError, match="AccessDenied"):
        execute(aws)
    assert not aws.target_writes
    assert aws.concurrency == {"app-a": None, "app-b": 2, "app-off": 0}
    assert aws.lock is None
