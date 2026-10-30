"""Tests for Reporting, Claude API integration, and Aggregator Lambda."""

import json
from decimal import Decimal

import boto3

from marketdata.contracts import iso
from services.api.app import handler
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
