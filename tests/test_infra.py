from pathlib import Path

import yaml


def test_queue_retries_schedules_and_iam_scope():
    template = yaml.safe_load(Path("infra/aws/template.yaml").read_text(encoding="utf-8"))
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
