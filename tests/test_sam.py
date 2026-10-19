from scripts.validate_template import translate


def test_sam_generates_consumers_and_queue_scoped_permissions():
    resources = translate()["Resources"]
    mappings = [r for r in resources.values() if r["Type"] == "AWS::Lambda::EventSourceMapping"]
    assert len(mappings) == 2
    assert all(
        r["Properties"]["FunctionResponseTypes"] == ["ReportBatchItemFailures"] for r in mappings
    )
    for name, queue in [("Normalizer", "TransformQueue"), ("CSVWorker", "CSVQueue")]:
        role = resources[name + "ExecutionRole"]["Properties"]
        statements = [s for p in role["Policies"] for s in p["PolicyDocument"]["Statement"]]
        polls = [s for s in statements if "sqs:ReceiveMessage" in s["Action"]]
        assert len(polls) == 1
        assert polls[0]["Resource"] == {"Fn::GetAtt": [queue, "Arn"]}
