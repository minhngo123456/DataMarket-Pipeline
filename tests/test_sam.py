from scripts.validate_template import translate


def test_sam_generates_consumers_and_queue_scoped_permissions():
    resources = translate()["Resources"]
    mappings = [r for r in resources.values() if r["Type"] == "AWS::Lambda::EventSourceMapping"]
    assert len(mappings) == 4
    assert all(
        r["Properties"]["FunctionResponseTypes"] == ["ReportBatchItemFailures"] for r in mappings
    )
    mapped_sources = {
        (
            item["Properties"]["FunctionName"]["Ref"],
            item["Properties"]["EventSourceArn"]["Fn::GetAtt"][0],
        )
        for item in mappings
    }
    assert mapped_sources == {
        ("Normalizer", "TransformQueue"),
        ("CSVWorker", "CSVQueue"),
        ("APIWorker", "APIQueue"),
        ("WebWorker", "WebQueue"),
    }
    for queue in ("APIQueue", "WebQueue"):
        properties = resources[queue]["Properties"]
        assert properties["VisibilityTimeout"] == 300
        assert properties["RedrivePolicy"]["maxReceiveCount"] == 5

    for name, queue in [
        ("Normalizer", "TransformQueue"),
        ("CSVWorker", "CSVQueue"),
        ("APIWorker", "APIQueue"),
        ("WebWorker", "WebQueue"),
    ]:
        role = resources[name + "ExecutionRole"]["Properties"]
        statements = [s for p in role["Policies"] for s in p["PolicyDocument"]["Statement"]]
        polls = [s for s in statements if "sqs:ReceiveMessage" in s["Action"]]
        assert len(polls) == 1
        assert polls[0]["Resource"] == {"Fn::GetAtt": [queue, "Arn"]}
        if name in ("APIWorker", "WebWorker"):
            assert "sqs:ChangeMessageVisibility" in polls[0]["Action"]
            sends = [s for s in statements if "sqs:SendMessage" in s["Action"]]
            assert len(sends) == 1
            assert sends[0]["Resource"] == {"Fn::GetAtt": ["TransformQueue", "Arn"]}
