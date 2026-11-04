from pathlib import Path

import yaml


def load_template(name="template.yaml"):
    return yaml.safe_load(Path("infra/aws", name).read_text(encoding="utf-8"))


def test_queue_retries_schedules_and_iam_scope():
    template = load_template()
    resources = template["Resources"]
    assert template["Globals"]["Function"]["Timeout"] == 45
    for name in ("CSVQueue", "APIQueue", "WebQueue", "TransformQueue", "ReportQueue"):
        props = resources[name]["Properties"]
        assert props["VisibilityTimeout"] >= 6 * 45
        assert props["RedrivePolicy"]["maxReceiveCount"] == 5
        assert props["RedrivePolicy"]["deadLetterTargetArn"] == {
            "Fn::GetAtt": [name + "DLQ", "Arn"]
        }
        assert resources[name + "DLQAlarm"]["Type"] == "AWS::CloudWatch::Alarm"
    for name in ("Normalizer", "CSVWorker"):
        props = resources[name]["Properties"]
        for source in props["Events"].values():
            assert source["Properties"]["FunctionResponseTypes"] == ["ReportBatchItemFailures"]
        role = resources[name + "ExecutionRole"]["Properties"]
        for policy in role["Policies"]:
            for statement in policy["PolicyDocument"]["Statement"]:
                assert statement["Resource"] != "*"
                assert not any(action.endswith(":*") for action in statement["Action"])
    report = resources["ReportDispatcherSchedule"]["Properties"]
    assert report["ScheduleExpressionTimezone"] == "Asia/Ho_Chi_Minh"
    assert report["ScheduleExpression"] == "cron(0 8 * * ? *)"
    assert template["Parameters"]["ScheduleState"]["Default"] == "DISABLED"
    assert "scheduled-time" in report["Target"]["Input"]
    for name in ("RawBucket", "ReportsBucket", "WebBucket"):
        assert all(resources[name]["Properties"]["PublicAccessBlockConfiguration"].values())


def test_each_consumer_visibility_covers_its_actual_timeout():
    template = load_template()
    resources = template["Resources"]
    for resource in resources.values():
        if resource["Type"] != "AWS::Serverless::Function":
            continue
        props = resource["Properties"]
        timeout = props.get("Timeout", template["Globals"]["Function"]["Timeout"])
        for event in props.get("Events", {}).values():
            if event["Type"] == "SQS":
                event_props = event["Properties"]
                queue = event_props["Queue"]["Fn::GetAtt"][0]
                visibility = resources[queue]["Properties"]["VisibilityTimeout"]
                assert visibility >= 6 * timeout + event_props.get("MaximumBatchingWindowInSeconds", 0)


def test_all_dlqs_alert_operations_with_encrypted_sns_and_restricted_publishers():
    resources = load_template()["Resources"]
    alarms = [r["Properties"] for name, r in resources.items() if name.endswith("DLQAlarm")]
    assert len(alarms) == 6
    assert all(a["AlarmActions"] == [{"Ref": "AlarmTopic"}] for a in alarms)
    assert resources["AlarmEmailSubscription"]["Condition"] == "HasAlarmEmail"
    assert resources["AlarmTopic"]["Properties"]["KmsMasterKeyId"] == {
        "Fn::GetAtt": ["NotificationKey", "Arn"]
    }
    policy = resources["AlarmTopicPolicy"]["Properties"]["PolicyDocument"]["Statement"]
    grant = next(s for s in policy if s["Effect"] == "Allow")
    assert grant["Principal"] == {"Service": "cloudwatch.amazonaws.com"}
    assert grant["Condition"]["StringEquals"]["aws:SourceAccount"] == {"Ref": "AWS::AccountId"}
    assert grant["Condition"]["ArnLike"]["aws:SourceArn"]["Fn::Sub"].endswith(
        ":alarm:${AWS::StackName}-*"
    )
    kms = resources["NotificationKey"]["Properties"]["KeyPolicy"]["Statement"]
    cw = next(s for s in kms if s["Sid"] == "AllowStackCloudWatchAlarms")
    assert cw["Condition"] == grant["Condition"]
    assert set(cw["Action"]) == {"kms:GenerateDataKey", "kms:Decrypt"}
    notification = resources["NotificationFunctionErrorsAlarm"]["Properties"]
    assert notification["AlarmName"] == {
        "Fn::Sub": "${AWS::StackName}-NotificationFunctionErrorsAlarm"
    }
    assert notification["AlarmActions"] == notification["OKActions"] == [{"Ref": "AlarmTopic"}]


def test_cloudfront_disabled_keeps_data_private_and_tls_denied():
    template = load_template()
    resources = template["Resources"]
    assert template["Parameters"]["EnableCloudFront"]["Default"] == "DISABLED"
    assert resources["WebOAC"]["Condition"] == "UseCloudFront"
    assert resources["WebDistribution"]["Condition"] == "UseCloudFront"
    assert template["Outputs"]["WebUrl"]["Condition"] == "UseCloudFront"
    for name in ("RawBucket", "ReportsBucket", "WebBucket"):
        assert resources[name]["DeletionPolicy"] == "Retain"
        assert resources[name]["UpdateReplacePolicy"] == "Retain"
        policy = resources[name + "Policy"]
        assert "Condition" not in policy
        statements = policy["Properties"]["PolicyDocument"]["Statement"]
        denies = [s for s in statements if s.get("Effect") == "Deny"]
        assert any(s["Condition"] == {"Bool": {"aws:SecureTransport": "false"}} for s in denies)


def test_report_credentials_are_arns_and_tenant_resources_are_namespaced():
    template = load_template()
    resources = template["Resources"]
    assert not {"AnthropicApiKey", "AnthropicSecretName"} & template["Parameters"].keys()
    props = resources["ReportAggregator"]["Properties"]
    env = props["Environment"]["Variables"]
    assert "ANTHROPIC_API_KEY" not in env
    assert "SNS_TOPIC_ARN" not in env and "SLACK_WEBHOOK_URL" not in env
    assert env["ANTHROPIC_SECRET_ARN"] == {"Ref": "AnthropicSecretArn"}
    assert env["GEMINI_SECRET_ARN"] == {"Ref": "GeminiSecretArn"}
    assert "GEMINI_API_KEY" not in env and "GeminiApiKey" not in template["Parameters"]
    assert env["NOTIFICATION_FUNCTION_NAME"] == {"Ref": "NotificationFunction"}
    notification = resources["NotificationFunction"]["Properties"]
    notification_env = notification["Environment"]["Variables"]
    assert notification_env["NOTIFICATION_STACK_NAME"] == {"Ref": "AWS::StackName"}
    assert notification_env["AWS_ACCOUNT_ID"] == {"Ref": "AWS::AccountId"}
    assert "SNS_TOPIC_ARN" not in notification_env and "SLACK_WEBHOOK_URL" not in notification_env
    statements = [s for policy in notification["Policies"] for s in policy["Statement"]]
    sns = next(s for s in statements if "sns:Publish" in s.get("Action", []))
    assert sns["Resource"]["Fn::Sub"].endswith(":${AWS::StackName}-tenant-*")
    slack = next(s for s in statements if "secretsmanager:GetSecretValue" in s.get("Action", []))
    assert slack["Resource"]["Fn::Sub"].endswith(":secret:${AWS::StackName}/tenants/*/slack-*")
    kms = next(s for s in statements if "kms:GenerateDataKey" in s.get("Action", []))
    assert kms["Resource"] == {"Fn::GetAtt": ["NotificationKey", "Arn"]}
    assert kms["Condition"] == {"StringEquals": {
        "kms:ViaService": {"Fn::Sub": "sns.${AWS::Region}.${AWS::URLSuffix}"}
    }}
    assert "NotificationTopic" not in resources
    assert "NotificationTopicArn" not in template["Outputs"]


def test_tenant_stack_keeps_report_destinations_private_and_separate():
    template = load_template("tenant-notifications.yaml")
    resources = template["Resources"]
    topic = resources["ReportTopic"]["Properties"]
    assert topic["TopicName"] == {"Fn::Sub": "${MainStackName}-tenant-${TenantId}"}
    assert topic["KmsMasterKeyId"] == {"Ref": "NotificationKeyArn"}
    secret = resources["SlackSecret"]
    assert secret["Properties"]["Name"] == {"Fn::Sub": "${MainStackName}/tenants/${TenantId}/slack"}
    assert "SecretString" not in secret["Properties"]
    assert "GenerateSecretString" not in secret["Properties"]
    assert secret["DeletionPolicy"] == "Retain"
    assert resources["EmailSubscription"]["Condition"] == "HasEmail"
    assert set(template["Outputs"]) == {"TenantId", "ReportTopicArn", "SlackSecretArn"}
    assert all("Export" not in output for output in template["Outputs"].values())


def test_sns_resource_policies_use_supported_topic_actions_and_require_tls():
    # SNS topic policies support fewer actions than IAM identity policies. A service
    # wildcard passes syntax lint but SetTopicAttributes rejects it on AWS.
    topic_actions = {
        "sns:AddPermission", "sns:DeleteTopic", "sns:GetDataProtectionPolicy",
        "sns:GetTopicAttributes", "sns:ListSubscriptionsByTopic", "sns:ListTagsForResource",
        "sns:Publish", "sns:PutDataProtectionPolicy", "sns:RemovePermission",
        "sns:SetTopicAttributes", "sns:Subscribe",
    }
    for filename in ("template.yaml", "tenant-notifications.yaml"):
        for resource in load_template(filename)["Resources"].values():
            if resource["Type"] != "AWS::SNS::TopicPolicy":
                continue
            statements = resource["Properties"]["PolicyDocument"]["Statement"]
            tls_denials = []
            for statement in statements:
                actions = statement["Action"]
                if isinstance(actions, str):
                    actions = [actions]
                assert actions and set(actions) <= topic_actions
                if statement["Effect"] == "Deny" and statement.get("Condition") == {
                    "Bool": {"aws:SecureTransport": "false"}
                }:
                    tls_denials.extend(actions)
            assert {"sns:Publish", "sns:Subscribe"} <= set(tls_denials)
