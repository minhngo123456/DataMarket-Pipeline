from scripts.validate_template import translate


def test_sam_generates_consumers_and_queue_scoped_permissions():
    resources = translate()["Resources"]
    mappings = [r for r in resources.values() if r["Type"] == "AWS::Lambda::EventSourceMapping"]
    assert len(mappings) == 5
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
        ("ReportAggregator", "ReportQueue"),
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
        ("ReportAggregator", "ReportQueue"),
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


def test_http_api_routes_reach_api_function_with_jwt():
    resources = translate()["Resources"]
    routes = [r["Properties"] for r in resources.values() if r["Type"] == "AWS::ApiGatewayV2::Route"]
    keys = {r["RouteKey"] for r in routes}
    for key in (
        "GET /me",
        "GET /matrix",
        "DELETE /sources/{id}",
        "PATCH /products/{id}/match",
        "PATCH /members/{id}",
    ):
        assert key in keys
    assert all(r["AuthorizationType"] == "JWT" for r in routes)
    assert all(r["Target"] == {"Fn::Sub": "integrations/${ApiIntegration}"} for r in routes)
    cors = resources["HttpApi"]["Properties"]["CorsConfiguration"]
    assert "DELETE" in cors["AllowMethods"]
    assert resources["ApiFunction"]["Properties"]["Handler"] == "services.api.app.handler"


def test_every_function_using_the_store_has_both_tables():
    # DynamoStore() reads both names from the environment, even when a handler only uses one table.
    resources = translate()["Resources"]
    functions = {
        name: resource["Properties"]["Environment"]["Variables"]
        for name, resource in resources.items()
        if resource["Type"] == "AWS::Lambda::Function" and "Environment" in resource["Properties"]
    }
    assert "NotificationFunction" in functions
    for name, variables in functions.items():
        if "CONTROL_TABLE" in variables:
            assert "OBSERVATIONS_TABLE" in variables, name


def test_aggregator_invokes_notification_lambda():
    resources = translate()["Resources"]
    env = resources["ReportAggregator"]["Properties"]["Environment"]["Variables"]
    assert env["NOTIFICATION_FUNCTION_NAME"] == {"Ref": "NotificationFunction"}
    role = resources["ReportAggregatorExecutionRole"]["Properties"]
    statements = [s for p in role["Policies"] for s in p["PolicyDocument"]["Statement"]]
    invokes = [s for s in statements if "lambda:InvokeFunction" in s["Action"]]
    assert invokes == [
        {"Effect": "Allow", "Action": ["lambda:InvokeFunction"], "Resource": {"Fn::GetAtt": ["NotificationFunction", "Arn"]}}
    ]


def test_api_and_raw_bucket_cors_allow_web_and_local_origins_without_duplicates():
    template = translate()
    resources = template["Resources"]
    expected = {
        "Fn::If": [
            "SingleWebOrigin",
            [{"Ref": "WebOrigin"}],
            [{"Ref": "WebOrigin"}, {"Ref": "LocalDevOrigin"}],
        ]
    }
    assert resources["HttpApi"]["Properties"]["CorsConfiguration"]["AllowOrigins"] == expected
    rules = resources["RawBucket"]["Properties"]["CorsConfiguration"]["CorsRules"]
    assert [rule["AllowedOrigins"] for rule in rules] == [expected]
    assert template["Conditions"]["SingleWebOrigin"] == {
        "Fn::Equals": [{"Ref": "WebOrigin"}, {"Ref": "LocalDevOrigin"}]
    }
    assert template["Parameters"]["LocalDevOrigin"]["Default"] == "http://localhost:5173"


def test_cloudfront_serves_index_for_react_routes():
    template = translate()
    config = template["Resources"]["WebDistribution"]["Properties"]["DistributionConfig"]
    assert config["CustomErrorResponses"] == [
        {"ErrorCode": code, "ResponseCode": 200, "ResponsePagePath": "/index.html", "ErrorCachingMinTTL": 0}
        for code in (403, 404)
    ]
    assert template["Outputs"]["WebDistributionId"] == {
        "Condition": "UseCloudFront",
        "Value": {"Ref": "WebDistribution"},
    }


def role_statements(resources, role):
    return [s for p in resources[role]["Properties"]["Policies"] for s in p["PolicyDocument"]["Statement"]]


def test_sns_publish_is_scoped_to_notification_topic():
    resources = translate()["Resources"]
    for role in ("ReportAggregatorExecutionRole", "NotificationFunctionRole"):
        publishes = [s for s in role_statements(resources, role) if "sns:Publish" in s["Action"]]
        assert len(publishes) == 1
        assert publishes[0]["Resource"] == {"Ref": "NotificationTopic"}


def test_secret_access_is_scoped_and_conditional():
    template = translate()
    resources = template["Resources"]
    assert not any("secretsmanager:GetSecretValue" in s["Action"] for s in role_statements(resources, "ReportAggregatorExecutionRole"))
    policy = resources["ReportAggregatorSecretPolicy"]
    assert policy["Condition"] == "HasAnthropicSecret"
    assert policy["Properties"]["RoleName"] == {"Ref": "ReportAggregatorExecutionRole"}
    statements = policy["Properties"]["PolicyDocument"]["Statement"]
    assert len(statements) == 1
    assert statements[0]["Action"] == ["secretsmanager:GetSecretValue"]
    assert statements[0]["Resource"]["Fn::Sub"].endswith(":secret:${AnthropicSecretName}-??????")
    assert template["Conditions"]["HasAnthropicSecret"] == {"Fn::Not": [{"Fn::Equals": [{"Ref": "AnthropicSecretName"}, ""]}]}
