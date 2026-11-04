import io
import json
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from scripts import smoke_test_aws, verify_aws
from services.ingestion.csv_worker import parse_csv


def test_smoke_requires_explicit_live_write_flag_before_creating_session(monkeypatch):
    session = Mock(side_effect=AssertionError("must not access AWS"))
    monkeypatch.setattr(smoke_test_aws, "new_session", session)
    with pytest.raises(SystemExit) as error:
        smoke_test_aws.main([])
    assert error.value.code == 2
    session.assert_not_called()


def test_fixture_has_fresh_timestamps_and_preserves_the_expected_90_10_split():
    now = datetime(2026, 11, 1, 5, 0, tzinfo=UTC)
    rows = parse_csv(smoke_test_aws.csv_fixture(now))
    assert len(rows) == 100
    assert {r["observed_at"] for r in rows} == {"2026-11-01T04:55:00+00:00"}
    assert sum(r["price"] == "invalid" for r in rows) == 10


@pytest.mark.parametrize("other_count,accepted", [(1, 90), (0, 89)])
def test_smoke_does_not_pass_on_wrong_counts_or_a_populated_other_tenant(other_count, accepted):
    store = Mock()
    store.product_history.side_effect = [[{}] * accepted, [{}] * other_count]
    with pytest.raises(RuntimeError, match="unexpected_csv_result"):
        smoke_test_aws.check_result(store, "a", "b", {"counts": smoke_test_aws.EXPECTED_COUNTS})


def test_successful_partition_check_reads_both_tenants():
    store = Mock()
    store.product_history.side_effect = [[{}] * 90, []]
    result = smoke_test_aws.check_result(store, "a", "b", {"counts": smoke_test_aws.EXPECTED_COUNTS})
    assert result["tenant_b_observations"] == 0
    assert store.product_history.call_args_list[1].args == ("b", "phone")


def test_retry_replays_the_same_version_and_checks_the_deployed_worker_response():
    client = Mock()
    client.invoke.return_value = {"Payload": io.BytesIO(b'{"batchItemFailures": []}')}
    smoke_test_aws.replay_csv(client, "worker", "raw", "tenants/a/raw/job", "version-one")
    event = json.loads(client.invoke.call_args.kwargs["Payload"])
    body = json.loads(event["Records"][0]["body"])
    assert body["detail"]["object"] == {"key": "tenants/a/raw/job", "version-id": "version-one"}
    assert client.invoke.call_args.kwargs["InvocationType"] == "RequestResponse"


@pytest.mark.parametrize("response", [
    {"FunctionError": "Unhandled", "Payload": io.BytesIO(b'{"errorMessage":"failed"}')},
    {"Payload": io.BytesIO(b'{"batchItemFailures":[{"itemIdentifier":"failed"}]}')},
])
def test_retry_failure_is_not_reported_as_passed(response):
    client = Mock()
    client.invoke.return_value = response
    with pytest.raises(RuntimeError, match="worker_replay_failed"):
        smoke_test_aws.replay_csv(client, "worker", "raw", "key", "version")


def test_normalizer_retry_waits_for_every_duplicate_batch():
    client = Mock()
    client.invoke.side_effect = lambda **kwargs: {"Payload": io.BytesIO(b'{"batchItemFailures": []}')}
    content = smoke_test_aws.csv_fixture(datetime(2026, 11, 1, 5, 0, tzinfo=UTC))
    result = smoke_test_aws.replay_normalizer(client, "normalizer", "a", "job", content)
    assert result == {"records": 100, "batch_failures": 0}
    assert client.invoke.call_count == 10
    last = json.loads(client.invoke.call_args.kwargs["Payload"])
    row = json.loads(last["Records"][-1]["body"])
    assert row["tenant_id"] == "a" and row["job_id"] == "job"
    assert row["row_number"] == 100


def test_job_timeout_and_failed_status_are_errors():
    store = Mock()
    store.get.return_value = {"status": "failed", "error": "invalid_csv_headers"}
    with pytest.raises(RuntimeError, match="invalid_csv_headers"):
        smoke_test_aws.wait_for_job(store, "a", "job", 30, clock=iter([0, 1]).__next__)
    with pytest.raises(TimeoutError):
        smoke_test_aws.wait_for_job(store, "a", "job", 30, clock=iter([0, 31]).__next__)


def test_inventory_follows_all_resource_pages():
    cfn = Mock()
    cfn.describe_stacks.return_value = {"Stacks": [{"StackStatus": "ROLLBACK_COMPLETE"}]}
    cfn.get_paginator.return_value.paginate.return_value = [
        {"StackResourceSummaries": [{"LogicalResourceId": "one"}]},
        {"StackResourceSummaries": [{"LogicalResourceId": "two"}]},
    ]
    _, resources = verify_aws.get_stack(cfn, "dev")
    assert [r["LogicalResourceId"] for r in resources] == ["one", "two"]


def test_failed_stack_report_keeps_retained_inventory_without_mutating_resources():
    cfn, sts = Mock(), Mock()
    cfn.describe_stacks.return_value = {"Stacks": [{
        "StackId": "stack-id", "StackStatus": "ROLLBACK_COMPLETE", "Parameters": [
            {"ParameterKey": "AnthropicApiKey", "ParameterValue": "never-include-me"},
            {"ParameterKey": "EnableCloudFront", "ParameterValue": "DISABLED"}],
    }]}
    cfn.get_paginator.return_value.paginate.return_value = [{"StackResourceSummaries": [{
        "LogicalResourceId": "RawBucket", "PhysicalResourceId": "retained-bucket",
        "ResourceType": "AWS::S3::Bucket", "ResourceStatus": "DELETE_SKIPPED",
    }]}]
    sts.get_caller_identity.return_value = {"Account": "123456789012"}
    session = Mock(region_name="us-east-1")
    session.client.side_effect = lambda name, **kwargs: {"cloudformation": cfn, "sts": sts}[name]
    report = verify_aws.inspect_stack(session, "dev")
    assert report["status"] == "needs_attention"
    assert report["retained_resources"][0]["PhysicalResourceId"] == "retained-bucket"
    assert "never-include-me" not in json.dumps(report)
    assert {call.args[0] for call in session.client.call_args_list} == {"cloudformation", "sts"}


def test_evidence_serializes_decimal_without_losing_precision(tmp_path):
    from decimal import Decimal

    output = tmp_path / "evidence.json"
    verify_aws.write_report({"price": Decimal("1.1234567890123456789")}, output)
    assert json.loads(output.read_text())["price"] == "1.1234567890123456789"


def test_healthy_stack_with_missing_resources_does_not_pass_preflight():
    cfn, sts = Mock(), Mock()
    cfn.describe_stacks.return_value = {"Stacks": [{
        "StackId": "stack-id", "StackStatus": "CREATE_COMPLETE", "Parameters": []}]}
    cfn.get_paginator.return_value.paginate.return_value = [{"StackResourceSummaries": []}]
    sts.get_caller_identity.return_value = {"Account": "123456789012"}
    clients = {name: Mock() for name in ("s3", "sqs", "lambda", "scheduler", "cloudwatch")}
    clients.update(cloudformation=cfn, sts=sts)
    session = Mock(region_name="us-east-1")
    session.client.side_effect = lambda name, **kwargs: clients[name]
    report = verify_aws.inspect_stack(session, "dev")
    assert report["status"] == "needs_attention"
    required = next(c for c in report["checks"] if c["name"] == "required_resources")
    assert "CSVWorker" in required["detail"]["missing"]
    assert "NotificationFunction" in required["detail"]["missing"]
    assert "NotificationFunctionErrorsAlarm" in required["detail"]["missing"]


def test_preflight_rejects_notification_function_without_tenant_namespace():
    cfn, sts, lambdas = Mock(), Mock(), Mock()
    cfn.describe_stacks.return_value = {"Stacks": [{
        "StackId": "stack-id", "StackStatus": "UPDATE_COMPLETE", "Parameters": []}]}
    cfn.get_paginator.return_value.paginate.return_value = [{"StackResourceSummaries": [{
        "LogicalResourceId": "NotificationFunction", "PhysicalResourceId": "notification-function",
        "ResourceType": "AWS::Lambda::Function", "ResourceStatus": "CREATE_COMPLETE",
    }]}]
    sts.get_caller_identity.return_value = {"Account": "123456789012"}
    lambdas.get_function_configuration.return_value = {
        "FunctionArn": "arn:aws:lambda:us-east-1:123456789012:function:notification-function",
        "Environment": {"Variables": {"SNS_TOPIC_ARN": "legacy-global-topic"}},
    }
    clients = {name: Mock() for name in ("s3", "sqs", "scheduler", "cloudwatch")}
    clients.update(cloudformation=cfn, sts=sts, **{"lambda": lambdas})
    session = Mock(region_name="us-east-1")
    session.client.side_effect = lambda name, **kwargs: clients[name]
    report = verify_aws.inspect_stack(session, "dev")
    checks = {check["name"]: check["passed"] for check in report["checks"]}
    assert not checks["notification_tenant_guard_configured"]
    assert not checks["notification_no_global_channel"]
    assert "legacy-global-topic" not in json.dumps(report)


def test_dlq_probe_preserves_other_messages_and_never_changes_queue_configuration():
    sqs = Mock()
    sqs.get_queue_attributes.return_value = {"Attributes": {
        "RedrivePolicy": json.dumps({"maxReceiveCount": 5,
                                     "deadLetterTargetArn": "arn:aws:sqs:us-east-1:123456789012:dlq"}),
        "VisibilityTimeout": "300"}}
    sqs.get_queue_url.return_value = {"QueueUrl": "dlq-url"}
    sqs.send_message.return_value = {"MessageId": "probe-id"}

    def receive(**kwargs):
        return {"Messages": [{"Body": "another job", "MessageId": "other-id"},
                             {"Body": sqs.send_message.call_args.kwargs["MessageBody"],
                              "MessageId": "probe-id", "Attributes": {"ApproximateReceiveCount": "6"}}]}

    sqs.receive_message.side_effect = receive
    session = Mock()
    session.client.return_value = sqs
    report = {}
    smoke_test_aws.check_dlq(session, {"TransformQueueUrl": "transform-url"}, report,
                            clock=iter([0, 1]).__next__)
    assert report["dlq_test"]["configured_max_receive_count"] == 5
    assert report["dlq_test"]["message_retained"] is True
    assert sqs.receive_message.call_args.kwargs["VisibilityTimeout"] == 0
    sqs.set_queue_attributes.assert_not_called()
    sqs.delete_message.assert_not_called()
