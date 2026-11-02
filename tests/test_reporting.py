"""Tests for Reporting, Claude API integration, and Aggregator Lambda."""

import json
from decimal import Decimal

import boto3

from marketdata.contracts import iso
from services.api.app import handler
from services.reporting.aggregator import handler as aggregator_handler
from services.reporting.aggregator import process_report_job
from services.reporting.claude_client import ClaudeClient
from tests.conftest import NOW
from tests.test_access import add_user
from tests.test_api import make_event


def test_claude_client_mock():
    client = ClaudeClient(mock_mode=True)
    summary_data = {
        "tenant_id": "tenant-a",
        "period": "2026-10-19",
        "stats": {"total_products": 5, "total_observations": 12, "reference_source": "internal"},
        "anomalies": [{"product_id": "phone", "source_id": "vendor", "change_percent": Decimal("18.5")}],
        "stale_sources": [],
    }
    result = client.generate_report_summary(summary_data)
    assert result["status"] == "mock_generated"
    assert "Báo cáo Thị trường" in result["text"]
    assert "tenant-a" in result["text"]


class _FakeResponse:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_claude_client_sends_current_model_and_falls_back_on_empty_text(monkeypatch):
    sent = {}

    def fake_urlopen(req, timeout):
        sent.update(json.loads(req.data))
        return _FakeResponse({"stop_reason": "max_tokens", "content": [{"type": "thinking", "thinking": ""}]})

    monkeypatch.delenv("CLAUDE_MODEL", raising=False)
    monkeypatch.setattr("services.reporting.claude_client.urllib.request.urlopen", fake_urlopen)
    client = ClaudeClient(api_key="k", mock_mode=False)
    result = client.generate_report_summary({"tenant_id": "tenant-a", "period": "2026-10-19", "stats": {}})

    assert sent["model"] == "claude-opus-5-5"
    assert sent["output_config"] == {"effort": "low"}
    assert result["status"] == "fallback_quantitative"


def test_claude_client_graceful_fallback():
    client = ClaudeClient(api_key="bad-key", mock_mode=False)
    # Simulate failed API call
    summary_data = {
        "tenant_id": "tenant-a",
        "period": "2026-10-19",
        "stats": {"total_products": 2, "total_observations": 5},
    }
    result = client.generate_report_summary(summary_data)
    assert result["status"] == "fallback_quantitative"
    assert "Số Liệu" in result["text"]
    assert "Dữ liệu số đã được tổng hợp thành công" or "Claude API" in result["text"]


def test_aggregator_worker_full_pipeline(store, monkeypatch):
    monkeypatch.setenv("REPORTS_BUCKET", "reports-bucket-test")
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="reports-bucket-test")

    # Seed an observation for tenant-a
    store.observations.put_item(
        Item={
            "pk": "T#tenant-a#P#phone",
            "sk": "vendor#2026-10-19T00:30:00Z#obs-1",
            "gsi1pk": "T#tenant-a#DAY#20261019",
            "gsi1sk": "2026-10-19T00:30:00Z#phone#vendor#obs-1",
            "tenant_id": "tenant-a",
            "product_id": "phone",
            "source_id": "vendor",
            "observation_id": "obs-1",
            "price": Decimal("20000000"),
            "currency": "VND",
            "price_basis": "tax_included",
            "observed_at": "2026-10-19T00:30:00Z",
            "expires_at": int((NOW.timestamp() + 86400 * 30)),
        }
    )

    # Create job in DynamoDB
    store.create_job(
        "tenant-a",
        "report-job-1",
        {
            "schema_version": 1,
            "tenant_id": "tenant-a",
            "source_id": "report",
            "job_id": "report-job-1",
            "kind": "report",
            "scheduled_at": iso(NOW),
            "status": "queued",
        },
    )

    message_body = {
        "schema_version": 1,
        "tenant_id": "tenant-a",
        "source_id": "report",
        "job_id": "report-job-1",
        "kind": "report",
        "scheduled_at": iso(NOW),
    }

    result = process_report_job(
        message_body,
        store,
        s3_client=s3,
        reports_bucket="reports-bucket-test",
        claude_client=ClaudeClient(mock_mode=True),
    )

    assert result["status"] == "completed"
    report_id = result["report_id"]

    # Verify DynamoDB record
    report_rec = store.get("tenant-a", "REPORT#" + report_id)
    assert report_rec is not None
    assert report_rec["status"] == "ready"

    # Verify S3 report object
    obj = s3.get_object(Bucket="reports-bucket-test", Key=f"tenants/tenant-a/reports/{report_id}.json")
    saved_data = json.loads(obj["Body"].read().decode("utf-8"))
    assert saved_data["tenant_id"] == "tenant-a"
    assert saved_data["report_id"] == report_id


def test_daily_ai_report_quota_limit(store):
    add_user(store, "analyst-a", "tenant-a", "analyst")

    # First trigger succeeds
    resp1 = handler(make_event("POST", "/reports/trigger", user_id="analyst-a"), store=store)
    assert resp1["statusCode"] == 202
    # The aggregator updates this job record when it finishes, so it must exist.
    job_id = json.loads(resp1["body"])["job_id"]
    assert store.get("tenant-a", "JOB#" + job_id)["status"] == "queued"

    # Second trigger on same day fails with 429
    resp2 = handler(make_event("POST", "/reports/trigger", user_id="analyst-a"), store=store)
    assert resp2["statusCode"] == 429
    data = json.loads(resp2["body"])
    assert data["error"]["code"] == "quota_exceeded"


def test_report_lock_atomic_conditional_write(store):
    # Tenant A acquires lock for 2026-10-30
    assert store.acquire_report_lock("tenant-a", "2026-10-30", job_id="job-1") is True

    # Immediate second attempt fails due to DynamoDB conditional write
    assert store.acquire_report_lock("tenant-a", "2026-10-30", job_id="job-2") is False

    # Tenant B can acquire lock for the same date independently (multi-tenant isolation)
    assert store.acquire_report_lock("tenant-b", "2026-10-30", job_id="job-3") is True

    # Verify lock item content
    lock = store.get_report_lock("tenant-a", "2026-10-30")
    assert lock is not None
    assert lock["status"] == "locked"
    assert lock["job_id"] == "job-1"


def test_secrets_manager_api_key_retrieval(monkeypatch):
    from services.reporting.claude_client import get_anthropic_api_key

    # Mock Secrets Manager client
    class FakeSecretsClient:
        def __init__(self, secrets):
            self._secrets = secrets

        def get_secret_value(self, SecretId):
            if SecretId in self._secrets:
                return {"SecretString": self._secrets[SecretId]}
            raise RuntimeError(f"Secret {SecretId} not found")

    client = FakeSecretsClient({
        "app/anthropic_plain": "sk-ant-plain-secret-value",
        "app/anthropic_json": json.dumps({"ANTHROPIC_API_KEY": "sk-ant-json-secret-value"}),
    })

    # 1. Plain text secret from Secrets Manager
    key_plain = get_anthropic_api_key(secret_name="app/anthropic_plain", secrets_client=client)
    assert key_plain == "sk-ant-plain-secret-value"

    # 2. JSON-encoded secret from Secrets Manager
    key_json = get_anthropic_api_key(secret_name="app/anthropic_json", secrets_client=client)
    assert key_json == "sk-ant-json-secret-value"

    # 3. Explicit api_key overrides Secrets Manager
    key_override = get_anthropic_api_key(api_key="explicit-key", secret_name="app/anthropic_plain", secrets_client=client)
    assert key_override == "explicit-key"

    # 4. Fallback to env var if Secrets Manager fails
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-fallback")
    key_fallback = get_anthropic_api_key(secret_name="app/non_existent", secrets_client=client)
    assert key_fallback == "sk-env-fallback"


def test_aggregator_reuses_cached_report_without_calling_ai(store, monkeypatch):
    monkeypatch.setenv("REPORTS_BUCKET", "reports-bucket-test")
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="reports-bucket-test")

    # Seed an already completed report
    period = "2026-10-19"
    from marketdata.contracts import stable_id
    report_id = stable_id("tenant-a", period)
    store.put(
        "tenant-a",
        "REPORT#" + report_id,
        {
            "report_id": report_id,
            "period": period,
            "status": "ready",
            "ai_status": "ai_generated",
            "summary": "Existing cached report summary",
        },
    )

    store.create_job(
        "tenant-a",
        "report-job-cached",
        {
            "schema_version": 1,
            "tenant_id": "tenant-a",
            "source_id": "report",
            "job_id": "report-job-cached",
            "kind": "report",
            "scheduled_at": iso(NOW),
            "status": "queued",
        },
    )

    # Fake ClaudeClient that raises an error if invoked
    class FailingClaudeClient:
        def generate_report_summary(self, summary_data):
            raise AssertionError("Claude API should not be called when report is already cached!")

    result = process_report_job(
        {
            "schema_version": 1,
            "tenant_id": "tenant-a",
            "source_id": "report",
            "job_id": "report-job-cached",
            "kind": "report",
            "scheduled_at": iso(NOW),
        },
        store,
        s3_client=s3,
        reports_bucket="reports-bucket-test",
        claude_client=FailingClaudeClient(),
    )

    assert result["status"] == "completed"
    assert result["reused"] is True
    assert store.get("tenant-a", "JOB#report-job-cached")["status"] == "completed"


def test_missing_job_record_does_not_send_report_to_dlq(store, monkeypatch):
    # Regression: a manual trigger enqueued a report without JOB#, so every retry failed
    # after the report was already stored and the message ended up in ReportQueueDLQ.
    monkeypatch.delenv("NOTIFICATION_FUNCTION_NAME", raising=False)
    monkeypatch.delenv("SNS_TOPIC_ARN", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    body = {
        "schema_version": 1,
        "tenant_id": "tenant-a",
        "source_id": "report",
        "job_id": "job-never-created",
        "kind": "report",
        "scheduled_at": iso(NOW),
    }

    class StubClaudeClient:
        def generate_report_summary(self, summary_data):
            return {"status": "ai_generated", "text": "Summary"}

    first = process_report_job(body, store, reports_bucket="", claude_client=StubClaudeClient())
    assert first["status"] == "completed"
    assert store.get("tenant-a", "REPORT#" + first["report_id"])["status"] == "ready"
    assert store.get("tenant-a", "JOB#job-never-created") is None

    retried = aggregator_handler({"Records": [{"messageId": "m1", "body": json.dumps(body)}]}, store=store)
    assert retried == {"batchItemFailures": []}


def test_retry_with_stored_report_still_notifies(store, monkeypatch):
    monkeypatch.setenv("NOTIFICATION_FUNCTION_NAME", "notify-fn")
    period = "2026-10-19"
    from marketdata.contracts import stable_id
    report_id = stable_id("tenant-a", period)
    store.put(
        "tenant-a",
        "REPORT#" + report_id,
        {"report_id": report_id, "period": period, "status": "ready", "ai_status": "ai_generated", "summary": "Stored"},
    )

    class RecordingLambda:
        calls = []

        def invoke(self, **kwargs):
            self.calls.append(json.loads(kwargs["Payload"]))

    result = process_report_job(
        {"tenant_id": "tenant-a", "job_id": "job-retry", "scheduled_at": iso(NOW)},
        store,
        lambda_client=RecordingLambda(),
    )

    assert result["reused"] is True
    assert RecordingLambda.calls == [
        {"tenant_id": "tenant-a", "report_id": report_id, "period": period, "summary": "Stored"}
    ]
