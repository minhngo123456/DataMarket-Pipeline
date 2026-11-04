"""Tests for Reporting, Claude API integration, and Aggregator Lambda."""

import json
from decimal import Decimal

import boto3
import pytest

from marketdata.contracts import iso
from services.api.app import handler
from services.reporting.aggregator import handler as aggregator_handler
from services.reporting.aggregator import process_report_job
from services.reporting.claude_client import ClaudeClient, GeminiClient, build_report_client
from services.reporting.templates.prompt_templates import build_claude_prompt
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


def test_claude_client_graceful_fallback(monkeypatch):
    def unavailable(*args, **kwargs):
        raise TimeoutError("simulated_timeout")

    monkeypatch.setattr("services.reporting.claude_client.urllib.request.urlopen", unavailable)
    client = ClaudeClient(api_key="bad-key", mock_mode=False)
    # Exercise the failure path without calling an external, potentially paid API.
    summary_data = {
        "tenant_id": "tenant-a",
        "period": "2026-10-19",
        "stats": {"total_products": 2, "total_observations": 5},
    }
    result = client.generate_report_summary(summary_data)
    assert result["status"] == "fallback_quantitative"
    assert "Số Liệu" in result["text"]
    assert "Claude API" in result["text"]


SUMMARY = {"tenant_id": "tenant-a", "period": "2026-10-19", "stats": {"total_products": 2, "total_observations": 5}}


def test_gemini_client_sends_the_key_only_in_its_header(monkeypatch):
    sent = {}

    def fake_urlopen(req, timeout):
        sent.update(url=req.full_url, key=req.get_header("X-goog-api-key"), body=json.loads(req.data), timeout=timeout)
        return _FakeResponse({
            "candidates": [{"content": {"parts": [{"text": "plan", "thought": True}, {"text": "Báo cáo thật"}]}}],
            "usageMetadata": {"totalTokenCount": 9},
        })

    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.setattr("services.reporting.claude_client.urllib.request.urlopen", fake_urlopen)
    result = GeminiClient(api_key="secret-key").generate_report_summary(SUMMARY)

    assert sent["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
    )
    assert sent["key"] == "secret-key"
    assert "secret-key" not in json.dumps(sent["body"])
    assert sent["body"]["contents"][0]["role"] == "user"
    assert sent["body"]["generationConfig"] == {"maxOutputTokens": 4000}
    assert sent["timeout"] == 90
    assert result["text"] == "Báo cáo thật"
    assert result["status"] == "ai_generated"
    assert result["provider"] == "gemini"
    assert result["model"] == "gemini-3.5-flash-lite"


def test_gemini_model_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.6-flash")
    assert GeminiClient(api_key="k").model == "gemini-3.6-flash"


@pytest.mark.parametrize(
    "body",
    [
        {"promptFeedback": {"blockReason": "SAFETY"}},
        {"candidates": [{"finishReason": "MAX_TOKENS", "content": {}}]},
        {"candidates": [{"content": {"parts": [{"text": "only thinking", "thought": True}]}}]},
    ],
)
def test_gemini_without_visible_text_falls_back(monkeypatch, body):
    monkeypatch.setattr(
        "services.reporting.claude_client.urllib.request.urlopen", lambda req, timeout: _FakeResponse(body)
    )
    result = GeminiClient(api_key="k").generate_report_summary(SUMMARY)
    assert result["status"] == "fallback_quantitative"
    assert "Gemini API" in result["text"]
    assert result["provider"] == "gemini"


def test_gemini_failure_does_not_leak_the_key(monkeypatch, caplog):
    def unavailable(req, timeout):
        raise TimeoutError(f"failed with {req.get_header('X-goog-api-key')}")

    monkeypatch.setattr("services.reporting.claude_client.urllib.request.urlopen", unavailable)
    result = GeminiClient(api_key="secret-key").generate_report_summary(SUMMARY)

    assert result["status"] == "fallback_quantitative"
    assert result["error_detail"] == "TimeoutError"
    assert "secret-key" not in result["text"]
    assert "secret-key" not in caplog.text


def test_gemini_reads_its_key_from_the_secret_arn(store, monkeypatch):
    secrets = boto3.client("secretsmanager", region_name="us-east-1")
    arn = secrets.create_secret(Name="gemini", SecretString=json.dumps({"api_key": "from-secret"}))["ARN"]
    keys = []

    def fake_urlopen(req, timeout):
        keys.append(req.get_header("X-goog-api-key"))
        return _FakeResponse({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    monkeypatch.setattr("services.reporting.claude_client.urllib.request.urlopen", fake_urlopen)
    client = GeminiClient(secret_arn=arn, secrets_client=secrets)

    assert client.generate_report_summary(SUMMARY)["status"] == "ai_generated"
    assert keys == ["from-secret"]


def test_report_client_prefers_gemini_then_claude_then_mock(monkeypatch):
    monkeypatch.delenv("GEMINI_SECRET_ARN", raising=False)
    monkeypatch.delenv("ANTHROPIC_SECRET_ARN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    mock = build_report_client()
    assert mock.mock_mode is True
    assert mock.generate_report_summary(SUMMARY)["status"] == "mock_generated"

    monkeypatch.setenv("ANTHROPIC_SECRET_ARN", "arn:anthropic")
    claude = build_report_client()
    assert claude.provider == "anthropic" and claude.mock_mode is False

    monkeypatch.setenv("GEMINI_SECRET_ARN", "arn:gemini")
    gemini = build_report_client()
    assert gemini.provider == "gemini" and gemini.mock_mode is False
    assert gemini.secret_arn == "arn:gemini"


VENDOR_PRICES = [
    {"product_id": "phone", "source_id": "vendor-a", "price": Decimal("100")},
    {"product_id": "phone", "source_id": "vendor-b", "price": Decimal("110")},
    {"product_id": "case", "source_id": "vendor-a", "price": Decimal("50")},
]


def test_prompt_without_a_reference_compares_sources_only():
    prompt = build_claude_prompt({
        "stats": {"total_products": 2, "total_observations": 3, "reference_source": None},
        "prices": VENDOR_PRICES,
    })
    assert "Nguồn có dữ liệu: vendor-a, vendor-b" in prompt
    assert "Nguồn tham chiếu: Không có" in prompt
    assert "- Sản phẩm phone: vendor-a 100 VND, vendor-b 110 VND; chênh lệch cao nhất so với thấp nhất: 10.00 %" in prompt
    assert "- Sản phẩm case: vendor-a 50 VND\n" in prompt
    assert "ổn định" not in prompt
    assert "Chênh lệch so với giá tham chiếu" not in prompt
    assert "internal" not in prompt


def test_prompt_with_a_reference_lists_its_differences():
    prompt = build_claude_prompt({
        "stats": {"reference_source": "internal"},
        "prices": VENDOR_PRICES,
        "top_changes": [{"product_id": "phone", "source_id": "vendor-b", "price": Decimal("110"),
                         "change_percent": Decimal("10.00")}],
    })
    assert "Nguồn tham chiếu: internal" in prompt
    assert "nguồn vendor-b có giá 110 VND (Chênh lệch: +10.00 %)" in prompt


def test_prompt_with_no_data_says_so():
    prompt = build_claude_prompt({"stats": {}, "stale_sources": ["vendor-a"]})
    assert "Không có quan sát giá hợp lệ" in prompt
    assert "Nguồn bị thiếu hoặc dữ liệu cũ quá 24h: vendor-a" in prompt
    assert "ổn định" not in prompt


def test_aggregator_gives_the_model_real_sources_and_no_invented_reference(store, monkeypatch):
    monkeypatch.delenv("NOTIFICATION_FUNCTION_NAME", raising=False)
    monkeypatch.setenv("REPORTS_BUCKET", "reports-bucket-test")
    boto3.client("s3").create_bucket(Bucket="reports-bucket-test")
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
            "price": Decimal("100"),
            "currency": "VND",
            "price_basis": "tax_included",
            "observed_at": "2026-10-19T00:30:00Z",
            "expires_at": int(NOW.timestamp()) + 86400,
        }
    )
    seen = {}

    class FakeClient:
        def generate_report_summary(self, summary):
            seen.update(summary)
            return {"text": "t", "status": "ai_generated", "provider": "gemini", "model": "m"}

    process_report_job(
        {"tenant_id": "tenant-a", "job_id": "job-prices", "scheduled_at": iso(NOW)}, store, claude_client=FakeClient()
    )

    assert seen["stats"]["reference_source"] is None
    assert seen["prices"] == [{"product_id": "phone", "source_id": "vendor", "price": Decimal("100")}]


def test_aggregator_records_the_provider_and_model(store, monkeypatch):
    monkeypatch.delenv("GEMINI_SECRET_ARN", raising=False)
    monkeypatch.delenv("ANTHROPIC_SECRET_ARN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("NOTIFICATION_FUNCTION_NAME", raising=False)
    monkeypatch.setenv("REPORTS_BUCKET", "reports-bucket-test")
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="reports-bucket-test")

    class FakeClient:
        def generate_report_summary(self, summary):
            return {"text": "t", "status": "ai_generated", "provider": "gemini", "model": "gemini-x"}

    result = process_report_job(
        {"tenant_id": "tenant-a", "job_id": "job-ai", "scheduled_at": iso(NOW)}, store, claude_client=FakeClient()
    )

    record = store.get("tenant-a", "REPORT#" + result["report_id"])
    assert (record["ai_provider"], record["ai_model"]) == ("gemini", "gemini-x")
    stored = json.loads(s3.get_object(Bucket="reports-bucket-test", Key=record["s3_key"])["Body"].read())
    assert (stored["ai_provider"], stored["ai_model"]) == ("gemini", "gemini-x")


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

    class FakeSecretsClient:
        def __init__(self, secrets):
            self._secrets = secrets

        def get_secret_value(self, SecretId):
            if SecretId in self._secrets:
                return {"SecretString": self._secrets[SecretId]}
            raise RuntimeError(f"Secret {SecretId} not found")

    monkeypatch.delenv("ANTHROPIC_SECRET_ARN", raising=False)
    client = FakeSecretsClient({"app/anthropic_json": json.dumps({"api_key": "test-json-key"})})
    key_json = get_anthropic_api_key(secret_name="app/anthropic_json", secrets_client=client)
    assert key_json == "test-json-key"

    key_override = get_anthropic_api_key(api_key="explicit-key", secret_name="app/non_existent", secrets_client=client)
    assert key_override == "explicit-key"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-env-key")
    assert get_anthropic_api_key() == "test-env-key"
    with pytest.raises(RuntimeError):
        get_anthropic_api_key(secret_name="app/non_existent", secrets_client=client)


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


def test_failed_notification_handoff_reuses_stored_report_on_retry(store, monkeypatch):
    from unittest.mock import MagicMock

    monkeypatch.setenv("NOTIFICATION_FUNCTION_NAME", "notify-fn")
    monkeypatch.delenv("REPORTS_BUCKET", raising=False)
    ai = MagicMock()
    ai.generate_report_summary.return_value = {"status": "ai_generated", "text": "Stored summary"}
    lam = MagicMock()
    lam.invoke.side_effect = [RuntimeError("throttled"), {}]
    body = {"tenant_id": "tenant-a", "job_id": "report-retry", "scheduled_at": iso(NOW)}

    with pytest.raises(RuntimeError, match="throttled"):
        process_report_job(body, store, claude_client=ai, lambda_client=lam)
    result = process_report_job(body, store, claude_client=ai, lambda_client=lam)

    assert result["reused"] is True and result["notification"]["status"] == "queued"
    assert ai.generate_report_summary.call_count == 1
    assert lam.invoke.call_count == 2
