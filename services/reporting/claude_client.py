"""Read a Claude credential and generate a report, with a numeric fallback."""

import json
import logging
import os
import urllib.request

from services.reporting.secrets import secret_field
from services.reporting.templates.prompt_templates import SYSTEM_PROMPT, build_claude_prompt

logger = logging.getLogger(__name__)


def get_anthropic_api_key(
    api_key: str | None = None,
    secret_name: str | None = None,
    secrets_client=None,
    *,
    secret_arn: str | None = None,
) -> str:
    """Read JSON api_key; secret_name remains an alias for older callers."""
    if api_key:
        return api_key
    target_secret = secret_arn or os.environ.get("ANTHROPIC_SECRET_ARN") or secret_name
    if target_secret:
        if secrets_client is not None:
            return secret_field(target_secret, "api_key", client=secrets_client)
        return secret_field(target_secret, "api_key")
    return os.environ.get("ANTHROPIC_API_KEY", "")


class ClaudeClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int = 4000,
        mock_mode: bool | None = None,
        secret_name: str | None = None,
        secrets_client=None,
        secret_arn: str | None = None,
    ):
        self.secret_arn = secret_arn or os.environ.get("ANTHROPIC_SECRET_ARN") or secret_name
        self._secrets_client = secrets_client
        self.api_key = api_key or ("" if self.secret_arn else os.environ.get("ANTHROPIC_API_KEY", ""))
        self.model = model or os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
        self.max_tokens = max_tokens
        if mock_mode is not None:
            self.mock_mode = mock_mode
        else:
            self.mock_mode = not bool(self.api_key or self.secret_arn) or os.environ.get("MOCK_AI", "false").lower() == "true"

    def generate_report_summary(self, summary_data: dict) -> dict:
        """Return generated text, or the available numeric summary on failure."""
        prompt = build_claude_prompt(summary_data)

        if self.mock_mode:
            return self._mock_response(summary_data)

        try:
            if self.secret_arn and not self.api_key:
                self.api_key = get_anthropic_api_key(
                    secret_arn=self.secret_arn, secrets_client=self._secrets_client
                )
            return self._call_anthropic_api(prompt)
        except Exception as exc:
            logger.warning(
                "Claude API unavailable (%s); using quantitative summary.",
                type(exc).__name__,
            )
            return self._fallback_response(summary_data, error_reason=type(exc).__name__)

    def _call_anthropic_api(self, user_prompt: str) -> dict:
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": SYSTEM_PROMPT,
            "output_config": {"effort": "low"},
            "messages": [
                {"role": "user", "content": user_prompt},
            ],
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        text_content = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_content += block.get("text", "")

        if data.get("stop_reason") == "refusal" or not text_content.strip():
            raise RuntimeError(f"no_report_text (stop_reason={data.get('stop_reason')})")

        return {
            "text": text_content.strip(),
            "status": "ai_generated",
            "token_usage": data.get("usage", {}),
            "model": self.model,
        }

    def _mock_response(self, summary_data: dict) -> dict:
        """Return labelled fixture text without calling the API."""
        tenant_id = summary_data.get("tenant_id", "demo-tenant")
        period = summary_data.get("period", "2026-10-19")
        stats = summary_data.get("stats", {})
        anomalies = summary_data.get("anomalies", [])

        anomaly_count = len(anomalies)
        mock_text = (
            f"### Báo cáo Thị trường Hằng ngày ({period})\n\n"
            f"Dữ liệu thử nghiệm cho {tenant_id}; chưa gọi Claude API.\n\n"
            f"- Sản phẩm: {stats.get('total_products', 0)}\n"
            f"- Quan sát giá: {stats.get('total_observations', 0)}\n"
            f"- Biến động vượt ngưỡng: {anomaly_count}"
        )

        return {
            "text": mock_text,
            "status": "mock_generated",
            "token_usage": {},
            "model": "local-fixture",
        }

    def _fallback_response(self, summary_data: dict, error_reason: str) -> dict:
        """Keep the numeric summary when the API or credential is unavailable."""
        period = summary_data.get("period", "Unknown")
        stats = summary_data.get("stats", {})

        fallback_text = (
            f"### Báo cáo Thị trường ({period}) - Dữ liệu Số Liệu\n\n"
            f"*Nhận định từ Claude API tạm thời không khả dụng. "
            f"Báo cáo dưới đây dùng số liệu đã tổng hợp.*\n\n"
            f"- Tổng sản phẩm theo dõi: {stats.get('total_products', 0)}\n"
            f"- Tổng số quan sát giá: {stats.get('total_observations', 0)}\n"
            f"- Trạng thái dữ liệu: Sẵn sàng tra cứu trên Dashboard."
        )

        return {
            "text": fallback_text,
            "status": "fallback_quantitative",
            "error_detail": error_reason,
            "model": "rule-based-fallback",
        }
