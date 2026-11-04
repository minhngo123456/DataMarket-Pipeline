import json

import boto3
import pytest

from scripts.e2e_failures import (
    ADMIN_EMAIL,
    HEADER_NAME,
    MISSING_ERRORS,
    TENANT,
    VIEWER_EMAIL,
    belongs_to_tenant,
    build_cleanup_plan,
    dead_letter_url,
    delete_cleanup_plan,
    header_problem,
    notification_problem,
    planned_writes,
    poll,
    provision,
    report_problem,
    retried_job_problem,
    start_job,
    tenant_messages,
    terminal_job_problem,
    unused_topic_arn,
)
from scripts.e2e_permissions import user_sub
from services.reporting.notification import tenant_channel

REAL_TENANT = "group07"
UNUSED_TOPIC = "arn:aws:sns:us-east-1:123456789012:none"


def test_terminal_job_needs_failed_status_and_a_known_error():
    assert terminal_job_problem({"status": "failed", "error": "api_http_error_404"}, MISSING_ERRORS) is None
    assert terminal_job_problem({"status": "failed", "error": "invalid_json_payload"}, MISSING_ERRORS) is None
    assert "got other" in terminal_job_problem({"status": "failed", "error": "other"}, MISSING_ERRORS)
    assert "got completed" in terminal_job_problem({"status": "completed"}, MISSING_ERRORS)
    assert "got None" in terminal_job_problem(None, MISSING_ERRORS)


def test_retried_job_needs_the_retry_limit_error():
    assert retried_job_problem({"status": "failed", "error": "retry_limit_exceeded_api_connection_error_x"}) is None
    assert retried_job_problem({"status": "failed", "error": "api_http_error_404"})
    assert retried_job_problem({"status": "normalizing"})
    assert retried_job_problem(None)


def test_header_problem_compares_the_returned_value():
    payload = {"data": [{"source_id": "a", "headers": {HEADER_NAME: "***"}}, {"source_id": "b"}]}
    assert header_problem(payload, "a", "***") is None
    assert "dummy" in header_problem(payload, "a", "dummy")
    assert "got None" in header_problem(payload, "b", "***")
    assert "not returned" in header_problem(payload, "c", "***")


def test_report_problem_expects_a_ready_report_that_skipped_ai():
    assert report_problem({"ai_status": "skipped_no_data", "status": "ready"}) is None
    assert report_problem({"ai_status": "fallback_quantitative", "status": "partial"})
    assert report_problem({"ai_status": "ai_generated", "status": "ready"})
    assert report_problem({"ai_status": "skipped_no_data", "status": "partial"})
    assert report_problem(None)


def test_notification_problem_expects_a_recorded_failure():
    failed = {"status": "failed", "sns_error": "sns_publish_failed", "sns_sent": False, "channels": []}
    assert notification_problem(failed) is None
    assert notification_problem({**failed, "status": "sent"})
    assert notification_problem({**failed, "sns_sent": True})
    assert notification_problem({**failed, "channels": ["sns"]})
    assert notification_problem(None)


def test_belongs_to_tenant_needs_an_exact_match():
    assert belongs_to_tenant(json.dumps({"tenant_id": TENANT}))
    assert not belongs_to_tenant(json.dumps({"tenant_id": TENANT + "0"}))
    assert not belongs_to_tenant(json.dumps(["list"]))
    assert not belongs_to_tenant("not json")


def test_poll_stops_at_the_first_truthy_value_or_the_deadline():
    values = iter([None, None, "done"])
    assert poll(lambda: next(values), timeout=60, sleep=lambda _: None) == "done"

    ticks = iter([0, 5, 11])
    assert poll(lambda: None, timeout=10, sleep=lambda _: None, clock=lambda: next(ticks)) is None


def test_unused_topic_passes_the_notification_namespace_check(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_STACK_NAME", "marketdata-dev")
    monkeypatch.setenv("AWS_REGION", "ap-southeast-1")
    monkeypatch.setenv("AWS_ACCOUNT_ID", "123456789012")
    stack_id = "arn:aws:cloudformation:ap-southeast-1:123456789012:stack/marketdata-dev/abc"

    arn = unused_topic_arn(stack_id, "marketdata-dev")

    assert arn == f"arn:aws:sns:ap-southeast-1:123456789012:marketdata-dev-tenant-{TENANT}"
    assert tenant_channel(TENANT, arn, "sns") == arn


def test_plan_names_only_the_test_tenant():
    plan = "\n".join(planned_writes())
    assert TENANT in plan and ADMIN_EMAIL in plan and VIEWER_EMAIL in plan
    assert REAL_TENANT not in plan


@pytest.fixture
def world(store):
    cognito = boto3.client("cognito-idp")
    pool_id = cognito.create_user_pool(PoolName="pool", UsernameAttributes=["email"])["UserPool"]["Id"]
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="reports")
    s3.put_bucket_versioning(Bucket="reports", VersioningConfiguration={"Status": "Enabled"})
    sqs = boto3.client("sqs")
    dlq = sqs.create_queue(QueueName="api-dlq")["QueueUrl"]
    dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    queue = sqs.create_queue(
        QueueName="api",
        Attributes={"RedrivePolicy": json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": 5})},
    )["QueueUrl"]

    store.put(REAL_TENANT, "META", {"status": "active"})
    store.put(REAL_TENANT, "MEMBER#real", {"role": "admin", "status": "active", "email": "real@example.com"})
    store.control.put_item(Item={"pk": "U#real", "sk": "MEMBERSHIP", "tenant_id": REAL_TENANT, "status": "active"})
    store.observations.put_item(Item={"pk": f"T#{REAL_TENANT}#P#phone", "sk": "keep", "tenant_id": REAL_TENANT})
    # Longer id sharing the prefix: cleanup must not spill into it.
    store.observations.put_item(Item={"pk": f"T#{TENANT}0#P#phone", "sk": "keep", "tenant_id": "x"})
    s3.put_object(Bucket="reports", Key=f"tenants/{REAL_TENANT}/reports/r.json", Body=b"keep")
    s3.put_object(Bucket="reports", Key=f"tenants/{TENANT}0/reports/r.json", Body=b"keep")
    return store, cognito, pool_id, s3, sqs, queue, dlq


def test_provision_keeps_both_users_in_the_test_tenant(world):
    store, cognito, pool_id, *_ = world
    assert set(provision(store, cognito, pool_id, UNUSED_TOPIC)) == {ADMIN_EMAIL, VIEWER_EMAIL}

    admin = user_sub(cognito, pool_id, ADMIN_EMAIL)
    viewer = user_sub(cognito, pool_id, VIEWER_EMAIL)
    assert store.get(TENANT, "MEMBER#" + admin)["role"] == "admin"
    assert store.get(TENANT, "MEMBER#" + viewer)["role"] == "viewer"
    assert store.get(TENANT, "META")["sns_topic_arn"] == UNUSED_TOPIC
    for sub in (admin, viewer):
        assert store.control.get_item(Key={"pk": "U#" + sub, "sk": "MEMBERSHIP"})["Item"]["tenant_id"] == TENANT
        assert store.get(REAL_TENANT, "MEMBER#" + sub) is None


def test_start_job_matches_what_the_api_worker_requires(world):
    from datetime import UTC, datetime

    store, _, _, _, sqs, queue, _ = world
    store.put(TENANT, "META", {"status": "active"})
    store.put(TENANT, "SOURCE#e2e-ssrf", {"source_id": "e2e-ssrf", "kind": "api", "enabled": True})
    job_id = start_job(store, sqs, queue, "e2e-ssrf", datetime.now(UTC))

    body = json.loads(sqs.receive_message(QueueUrl=queue)["Messages"][0]["Body"])
    assert body["kind"] == "api" and body["job_id"] == job_id and body["schema_version"] == 1
    store.require_job({"tenant_id": TENANT, "source_id": "e2e-ssrf", "job_id": job_id})


def test_tenant_messages_leaves_other_tenants_in_the_queue(world):
    *_, sqs, _, dlq = world
    sqs.send_message(QueueUrl=dlq, MessageBody=json.dumps({"tenant_id": TENANT, "job_id": "mine"}))
    sqs.send_message(QueueUrl=dlq, MessageBody=json.dumps({"tenant_id": REAL_TENANT, "job_id": "theirs"}))
    sqs.send_message(QueueUrl=dlq, MessageBody="not json")

    assert [body["job_id"] for body in tenant_messages(sqs, dlq, delete=True)] == ["mine"]
    sqs.set_queue_attributes(QueueUrl=dlq, Attributes={"VisibilityTimeout": "0"})
    remaining = sqs.get_queue_attributes(
        QueueUrl=dlq, AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"]
    )["Attributes"]
    assert sum(int(value) for value in remaining.values()) == 2


def test_reading_leaves_messages_visible_for_a_cleanup_right_after(world):
    *_, sqs, _, dlq = world
    sqs.send_message(QueueUrl=dlq, MessageBody=json.dumps({"tenant_id": TENANT, "job_id": "mine"}))

    assert len(tenant_messages(sqs, dlq)) == 1
    assert len(tenant_messages(sqs, dlq, delete=True)) == 1
    assert tenant_messages(sqs, dlq) == []


def test_dead_letter_url_follows_the_redrive_policy(world):
    *_, sqs, queue, dlq = world
    assert dead_letter_url(sqs, queue) == dlq


def test_cleanup_removes_only_test_data(world):
    store, cognito, pool_id, s3, sqs, _, dlq = world
    provision(store, cognito, pool_id, UNUSED_TOPIC)
    admin = user_sub(cognito, pool_id, ADMIN_EMAIL)
    store.put(TENANT, "SOURCE#e2e-ssrf", {"kind": "api"})
    store.observations.put_item(Item={"pk": f"T#{TENANT}#P#phone", "sk": "drop", "tenant_id": TENANT})
    s3.put_object(Bucket="reports", Key=f"tenants/{TENANT}/reports/r.json", Body=b"drop")
    sqs.send_message(QueueUrl=dlq, MessageBody=json.dumps({"tenant_id": TENANT}))
    sqs.send_message(QueueUrl=dlq, MessageBody=json.dumps({"tenant_id": REAL_TENANT}))

    plan = build_cleanup_plan(store, cognito, pool_id, s3, ["reports"], sqs, [dlq])
    assert set(plan["users"]) == {ADMIN_EMAIL, VIEWER_EMAIL}
    assert plan["dead_letters"] == {dlq: 1}
    sqs.set_queue_attributes(QueueUrl=dlq, Attributes={"VisibilityTimeout": "0"})
    delete_cleanup_plan(plan, store, cognito, pool_id, s3, sqs)

    assert cognito.list_users(UserPoolId=pool_id)["Users"] == []
    assert store.get(TENANT, "META") is None
    assert store.get(TENANT, "SOURCE#e2e-ssrf") is None
    assert "Item" not in store.control.get_item(Key={"pk": "U#" + admin, "sk": "MEMBERSHIP"})

    assert store.get(REAL_TENANT, "META")
    assert store.get(REAL_TENANT, "MEMBER#real")
    assert "Item" in store.control.get_item(Key={"pk": "U#real", "sk": "MEMBERSHIP"})
    remaining = {item["pk"] for item in store.observations.scan()["Items"]}
    assert remaining == {f"T#{REAL_TENANT}#P#phone", f"T#{TENANT}0#P#phone"}
    keys = {v["Key"] for v in s3.list_object_versions(Bucket="reports").get("Versions", [])}
    assert keys == {f"tenants/{REAL_TENANT}/reports/r.json", f"tenants/{TENANT}0/reports/r.json"}
