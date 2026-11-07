import json
from unittest.mock import MagicMock

import boto3
import pytest

from marketdata.contracts import AccessDenied
from services.api.auth import AuthContext
from services.api.routes.reports import handle_get_report_detail
from services.reporting import secrets
from services.reporting.claude_client import ClaudeClient
from services.reporting.notification import dispatch_notification, send_slack_webhook


def test_json_secret_is_read_from_secrets_manager(store):
    client = boto3.client("secretsmanager")
    arn = client.create_secret(Name="claude", SecretString='{"api_key":"test-key"}')["ARN"]
    assert secrets.secret_field(arn, "api_key", client=client) == "test-key"
    client.put_secret_value(SecretId=arn, SecretString='{"wrong_field":"test-key"}')
    with pytest.raises(ValueError, match="secret_field_missing"):
        secrets.secret_field(arn, "api_key", client=client)


def test_secret_cache_expires(monkeypatch):
    client = MagicMock()
    client.get_secret_value.return_value = {"SecretString": '{"api_key":"first"}'}
    monkeypatch.setattr(secrets, "_client", client)
    monkeypatch.setattr(secrets, "_cache", {})
    clock = [100]
    monkeypatch.setattr(secrets.time, "monotonic", lambda: clock[0])
    assert secrets.secret_field("test-arn", "api_key") == "first"
    client.get_secret_value.return_value = {"SecretString": '{"api_key":"rotated"}'}
    clock[0] = 200
    assert secrets.secret_field("test-arn", "api_key") == "first"
    clock[0] = 401
    assert secrets.secret_field("test-arn", "api_key") == "rotated"
    assert client.get_secret_value.call_count == 2


def test_claude_uses_secret_without_plaintext_environment(store, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MOCK_AI", raising=False)
    monkeypatch.setenv("ANTHROPIC_SECRET_ARN", "configured-secret")
    resolver = MagicMock(return_value="test-key")
    monkeypatch.setattr("services.reporting.claude_client.secret_field", resolver)
    client = ClaudeClient()
    monkeypatch.setattr(client, "_call_anthropic_api", lambda prompt: {"text": "ok", "status": "ai_generated"})
    result = client.generate_report_summary({"stats": {}})
    assert result["status"] == "ai_generated"
    assert client.api_key == "test-key"
    resolver.assert_called_once_with("configured-secret", "api_key")


def test_failed_secret_does_not_leak_credentials_into_report_or_log(store, monkeypatch, caplog):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_SECRET_ARN", "configured-secret")
    monkeypatch.delenv("MOCK_AI", raising=False)

    def denied(*args):
        raise RuntimeError("sensitive-secret-value")

    monkeypatch.setattr("services.reporting.claude_client.secret_field", denied)
    result = ClaudeClient().generate_report_summary({"stats": {}})
    assert result["status"] == "fallback_quantitative"
    assert "sensitive-secret-value" not in json.dumps(result)
    assert "sensitive-secret-value" not in caplog.text


def test_configured_secret_overrides_env_key_and_is_loaded_lazily(store, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_SECRET_ARN", "configured-arn")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "unused-env-key")
    secret_client = MagicMock()
    secret_client.get_secret_value.return_value = {"SecretString": '{"api_key":"secret-key"}'}
    client = ClaudeClient(secret_name="legacy-name", secrets_client=secret_client)
    secret_client.get_secret_value.assert_not_called()
    monkeypatch.setattr(client, "_call_anthropic_api", lambda prompt: {"text": "ok", "status": "ai_generated"})

    assert client.generate_report_summary({"stats": {}})["status"] == "ai_generated"
    assert client.api_key == "secret-key"
    secret_client.get_secret_value.assert_called_once_with(SecretId="configured-arn")


def test_mock_mode_does_not_read_a_configured_secret(store, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_SECRET_ARN", "configured-arn")
    secret_client = MagicMock()
    result = ClaudeClient(mock_mode=True, secrets_client=secret_client).generate_report_summary({"stats": {}})
    assert result["status"] == "mock_generated" and result["token_usage"] == {}
    assert "Dữ liệu thử nghiệm" in result["text"]
    secret_client.get_secret_value.assert_not_called()


def test_secret_failure_does_not_switch_to_environment_key(store, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_SECRET_ARN", "configured-arn")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "unused-env-key")
    secret_client = MagicMock()
    secret_client.get_secret_value.side_effect = RuntimeError("sensitive-secret-value")
    client = ClaudeClient(secrets_client=secret_client)
    request = MagicMock()
    monkeypatch.setattr(client, "_call_anthropic_api", request)
    assert client.generate_report_summary({"stats": {}})["status"] == "fallback_quantitative"
    request.assert_not_called()


def test_global_notification_channels_are_not_shared_between_tenants(store, monkeypatch):
    monkeypatch.setenv("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:global")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/other-tenant")
    sns = MagicMock()
    result = dispatch_notification("tenant-a", "report-1", "2026-11-01", "summary", store, sns)
    assert result["status"] == "none_configured"
    sns.publish.assert_not_called()


@pytest.mark.parametrize("service", ["sns", "secretsmanager"])
def test_notification_rejects_other_tenant_channel(store, monkeypatch, service):
    monkeypatch.setenv("NOTIFICATION_STACK_NAME", "marketdata-dev")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCOUNT_ID", "123456789012")
    field = "sns_topic_arn" if service == "sns" else "slack_secret_arn"
    resource = "marketdata-dev-tenant-tenant-b" if service == "sns" else "secret:marketdata-dev/tenants/tenant-b/slack-abcdef"
    store.put("tenant-a", "META", {
        "status": "active",
        "sns_topic_arn": "arn:aws:sns:us-east-1:123456789012:marketdata-dev-tenant-tenant-a",
        field: f"arn:aws:{service}:us-east-1:123456789012:{resource}",
    })
    sns = MagicMock()
    with pytest.raises(AccessDenied, match="wrong_tenant"):
        dispatch_notification("tenant-a", "report-1", "2026-11-01", "summary", store, sns)
    sns.publish.assert_not_called()


def test_tenant_slack_uses_json_secret(store, monkeypatch):
    store.put("tenant-a", "META", {"status": "active", "slack_secret_arn": "slack-secret"})
    resolver = MagicMock(return_value="https://hooks.slack.com/services/test")
    sender = MagicMock(return_value=True)
    monkeypatch.setattr("services.reporting.notification.secret_field", resolver)
    monkeypatch.setattr("services.reporting.notification.send_slack_webhook", sender)
    result = dispatch_notification("tenant-a", "report-1", "2026-11-01", "summary", store)
    assert result["channels"] == ["slack"]
    resolver.assert_called_once_with("slack-secret", "webhook_url")


@pytest.mark.parametrize("url", ["http://hooks.slack.com/services/a", "https://example.com/a", "https://hooks.slack.com.evil.test/services/a"])
def test_slack_rejects_untrusted_endpoint_without_http_request(url, monkeypatch):
    request = MagicMock()
    monkeypatch.setattr("services.reporting.notification.urllib.request.urlopen", request)
    with pytest.raises(ValueError, match="invalid_slack"):
        send_slack_webhook(url, "summary", "tenant-a", "2026-11-01")
    request.assert_not_called()


def detail(store, s3, report_id="report-1"):
    response = handle_get_report_detail(
        {"pathParameters": {"id": report_id}}, store,
        AuthContext("admin-a", "tenant-a", "admin", {}),
        s3_client=s3, reports_bucket_name="reports",
    )
    return json.loads(response["body"])


def reports_bucket(store):
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="reports")
    store.put("tenant-a", "REPORT#report-1", {
        "report_id": "report-1", "summary": "preview cut at 1000 cha",
        "s3_key": "tenants/tenant-a/reports/report-1.json",
    })
    return s3


def test_report_detail_returns_the_full_stored_text(store):
    s3 = reports_bucket(store)
    full_text = "Đoạn mở đầu. " * 200 + "Câu cuối cùng."
    s3.put_object(Bucket="reports", Key="tenants/tenant-a/reports/report-1.json", Body=json.dumps({
        "report_id": "report-1", "summary": full_text,
        "metrics": {"total_products": 5}, "anomalies": [{"product_id": "phone"}], "stale_sources": ["vendor-c"],
    }))

    body = detail(store, s3)

    assert body["summary"] == full_text and len(body["summary"]) > 1000
    assert body["metrics"] == {"total_products": 5}
    assert body["anomalies"] == [{"product_id": "phone"}]
    assert body["stale_sources"] == ["vendor-c"]


@pytest.mark.parametrize("stored", [None, b"not json", json.dumps({"report_id": "other", "summary": "x"})])
def test_report_detail_falls_back_to_the_preview(store, stored):
    s3 = reports_bucket(store)
    if stored is not None:
        s3.put_object(Bucket="reports", Key="tenants/tenant-a/reports/report-1.json", Body=stored)

    assert detail(store, s3)["summary"] == "preview cut at 1000 cha"


def test_report_detail_ignores_an_oversized_object(store):
    s3 = reports_bucket(store)
    s3.put_object(Bucket="reports", Key="tenants/tenant-a/reports/report-1.json",
                  Body=json.dumps({"report_id": "report-1", "summary": "x" * (1024 * 1024)}))

    assert detail(store, s3)["summary"] == "preview cut at 1000 cha"


def test_report_presign_rejects_other_tenant_object_key(store):
    store.put("tenant-a", "REPORT#report-1", {"s3_key": "tenants/tenant-b/reports/report-1.json"})
    signer = MagicMock()
    with pytest.raises(AccessDenied, match="report_object_not_in_tenant"):
        handle_get_report_detail(
            {"pathParameters": {"id": "report-1"}}, store,
            AuthContext("admin-a", "tenant-a", "admin", {}),
            s3_client=signer, reports_bucket_name="reports",
        )
    signer.generate_presigned_url.assert_not_called()
