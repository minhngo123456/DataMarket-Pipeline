"""Deployment safety checks; these tests never contact AWS."""

import json
from argparse import Namespace
from unittest.mock import Mock

import pytest
from cfnlint.decode import decode

from scripts import demo_guard as cli

ACCOUNT = "972243443945"
STACK_ID = f"arn:aws:cloudformation:us-east-1:{ACCOUNT}:stack/marketdata-hoang-dev/old-id"


@pytest.fixture
def args():
    return Namespace(account=ACCOUNT, region="us-east-1", profile="test",
                     target_stack="marketdata-hoang-dev", guard_stack="marketdata-hoang-demo-guard",
                     email="test@example.com", artifact_bucket="existing-artifacts")


@pytest.mark.parametrize("change", [{"account": "000000000000"}, {"region": "ap-southeast-1"},
                                     {"target_stack": "another-project"},
                                     {"guard_stack": "marketdata-hoang-dev"}])
def test_wrong_account_or_scope_is_refused(monkeypatch, args, change):
    sts = Mock()
    sts.get_caller_identity.return_value = {"Account": ACCOUNT}
    monkeypatch.setattr(cli.boto3, "Session", Mock(return_value=object()))
    monkeypatch.setattr(cli, "client", lambda session, name: sts)
    for key, value in change.items():
        setattr(args, key, value)
    with pytest.raises(ValueError):
        cli.checked_session(args)


def saved_plan(tmp_path, monkeypatch, args):
    plan = {key: getattr(args, key) for key in ("account", "region", "target_stack", "guard_stack")}
    plan.update(target_stack_id=STACK_ID, change_set_id="change-set-id", change_set_type="CREATE")
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    monkeypatch.setattr(cli, "PLAN_PATH", path)


def test_apply_refuses_recreated_target_stack(monkeypatch, tmp_path, args):
    saved_plan(tmp_path, monkeypatch, args)
    cfn = Mock()
    cfn.describe_stacks.return_value = {"Stacks": [{"StackId": STACK_ID + "-recreated",
                                                   "StackStatus": "UPDATE_COMPLETE"}]}
    monkeypatch.setattr(cli, "client", lambda session, name: cfn)
    with pytest.raises(ValueError, match="Target stack changed"):
        cli.apply_plan(object(), args)
    cfn.describe_change_set.assert_not_called()
    cfn.execute_change_set.assert_not_called()


@pytest.mark.parametrize("action", ["Modify", "Remove"])
def test_new_guard_cannot_modify_or_remove_resources(monkeypatch, tmp_path, args, action):
    saved_plan(tmp_path, monkeypatch, args)
    cfn = Mock()
    cfn.describe_stacks.return_value = {"Stacks": [{"StackId": STACK_ID, "StackStatus": "UPDATE_COMPLETE"}]}
    cfn.describe_change_set.return_value = {
        "Status": "CREATE_COMPLETE", "ExecutionStatus": "AVAILABLE",
        "Changes": [{"ResourceChange": {"Action": action}}],
    }
    monkeypatch.setattr(cli, "client", lambda session, name: cfn)
    with pytest.raises(ValueError, match="unexpectedly modifies or removes"):
        cli.apply_plan(object(), args)
    cfn.execute_change_set.assert_not_called()


def test_prepare_update_preserves_original_deadline(monkeypatch, tmp_path, args):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "PLAN_PATH", tmp_path / "plan.json")
    source = tmp_path / "services/demo_guard/app.py"
    source.parent.mkdir(parents=True)
    source.write_text("def handler(event, context): return event\n", encoding="utf-8")
    template = tmp_path / "infra/aws/demo-guard.yaml"
    template.parent.mkdir(parents=True)
    template.write_text("Resources: {}\n", encoding="utf-8")
    previous = {"TargetStackId": STACK_ID, "BudgetName": "original-budget",
                "BudgetStartUtc": "2026-11-01T00:00:00Z", "BudgetEndUtc": "2026-11-12T00:00:00Z",
                "DeadlineUtc": "2026-11-08T12:34:56Z", "DeadlineExpression": "at(2026-11-08T12:34:56)"}
    existing = {"StackStatus": "CREATE_COMPLETE", "Parameters": [
        {"ParameterKey": key, "ParameterValue": value} for key, value in previous.items()]}
    monkeypatch.setattr(cli, "find_guard", lambda cfn, name: existing)
    scope = {key: ["resource-arn"] for key in ("FunctionArns", "MappingArns", "ScheduleArns",
                                              "ScheduleRoleArns", "RuleArns")}
    monkeypatch.setattr(cli, "target_inventory", lambda session, args: ({"StackId": STACK_ID}, scope))
    services = {name: Mock() for name in ("cloudformation", "lambda", "s3")}
    services["lambda"].get_account_settings.return_value = {"AccountLimit": {"ConcurrentExecutions": 5}}
    services["cloudformation"].create_change_set.return_value = {"Id": "prepared-change-set"}
    monkeypatch.setattr(cli, "client", lambda session, name: services[name])

    cli.prepare(object(), args)

    request = services["cloudformation"].create_change_set.call_args.kwargs
    actual = {item["ParameterKey"]: item["ParameterValue"] for item in request["Parameters"]}
    assert all(actual[key] == value for key, value in previous.items())
    assert request["ChangeSetType"] == "UPDATE"
    assert actual["AlertEmail"] == args.email
    services["cloudformation"].execute_change_set.assert_not_called()


def test_service_permission_requires_exact_source_account_and_arn():
    statement = {"Effect": "Allow", "Principal": {"Service": "sns.amazonaws.com"},
                 "Action": "lambda:InvokeFunction", "Resource": "guard-arn",
                 "Condition": {"ArnLike": {"AWS:SourceArn": "topic-arn"},
                               "StringEquals": {"AWS:SourceAccount": ACCOUNT}}}
    policy = {"Statement": [statement]}
    assert cli.allows_service(policy, "sns.amazonaws.com", "lambda:InvokeFunction",
                              "guard-arn", ACCOUNT, "topic-arn")
    statement["Condition"]["ArnLike"]["AWS:SourceArn"] = "*"
    assert not cli.allows_service(policy, "sns.amazonaws.com", "lambda:InvokeFunction",
                                  "guard-arn", ACCOUNT, "topic-arn")


@pytest.fixture
def guard_resources():
    template, errors = decode(str(cli.ROOT / "infra/aws/demo-guard.yaml"))
    assert not errors
    return template["Resources"]


def test_guard_sns_policy_statements_have_unique_ids(guard_resources):
    # SetTopicAttributes rejected missing IDs during deployment even though lint passed.
    for resource in guard_resources.values():
        if resource["Type"] == "AWS::SNS::TopicPolicy":
            statements = resource["Properties"]["PolicyDocument"]["Statement"]
            identifiers = [statement.get("Sid") for statement in statements]
            assert all(isinstance(value, str) and value.strip() for value in identifiers)
            assert len(identifiers) == len(set(identifiers))


def test_only_stop_threshold_reaches_guard_lambda(guard_resources):
    notifications = guard_resources["Budget"]["Properties"]["NotificationsWithSubscribers"]
    warning = next(item for item in notifications if item["Notification"]["Threshold"] == 5)
    assert all(subscriber["SubscriptionType"] == "EMAIL" for subscriber in warning["Subscribers"])
    sns_paths = [(item["Notification"]["Threshold"], subscriber["Address"])
                 for item in notifications for subscriber in item["Subscribers"]
                 if subscriber["SubscriptionType"] == "SNS"]
    assert sns_paths == [(8, {"Ref": "StopTopic"})]
    subscriptions = [resource["Properties"] for resource in guard_resources.values()
                     if resource["Type"] == "AWS::SNS::Subscription"
                     and resource["Properties"]["TopicArn"] == {"Ref": "StopTopic"}]
    assert len(subscriptions) == 1
    assert subscriptions[0]["Protocol"] == "lambda"
    assert subscriptions[0]["Endpoint"] == {"Fn::GetAtt": ["GuardFunction", "Arn"]}


def test_guard_cannot_delete_application_resources(guard_resources):
    statements = [statement for policy in guard_resources["GuardRole"]["Properties"]["Policies"]
                  for statement in policy["PolicyDocument"]["Statement"]]
    for statement in statements:
        actions = statement["Action"]
        actions = actions if isinstance(actions, list) else [actions]
        assert statement["Resource"] != "*"
        assert all("*" not in action for action in actions)
        assert not any(action.startswith(("dynamodb:", "kms:", "secretsmanager:")) for action in actions)
        for action in actions:
            if ":Delete" in action:
                assert action in {"s3:DeleteObject", "lambda:DeleteFunctionConcurrency"}
            if action in {"s3:PutObject", "s3:DeleteObject"}:
                assert statement["Resource"] == [
                    {"Fn::Sub": "${StateBucket.Arn}/pause-state.json"},
                    {"Fn::Sub": "${StateBucket.Arn}/guard-lock.json"},
                ]
            elif action == "lambda:DeleteFunctionConcurrency":
                assert statement["Resource"] == {"Ref": "FunctionArns"}
            elif action == "cloudformation:ListStackResources":
                assert statement["Resource"] == {"Ref": "TargetStackId"}
