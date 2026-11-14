"""Tenant/source-scoped profiles and durable, conservative AI call reservations."""

import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from marketdata.contracts import InvalidRecord, identifier, stable_id, timestamp
from marketdata.repository import generation_condition, pk
from services.ingestion.ai_client import GeminiRulesClient
from services.ingestion.ai_rules import AIExtractor


def authorize_live(tenant_id):
    try:
        until = timestamp(os.environ.get("WEB_AI_APPROVED_UNTIL"))
        now = datetime.now(UTC)
        if not now < until <= now + timedelta(hours=24):
            raise ValueError
        allowed = [value.strip() for value in os.environ.get("WEB_AI_TENANT_ALLOWLIST", "").split(",")]
        if tenant_id not in allowed or not os.environ.get("WEB_AI_USAGE_APPROVAL_REF"):
            raise ValueError
    except (ValueError, TypeError):
        raise InvalidRecord("web_ai_approval_required") from None


class LazyGemini:
    def suggest(self, prompt):
        return GeminiRulesClient().suggest(prompt)


class AIState:
    def __init__(self, store, event, source, live):
        self.store, self.event, self.source, self.live = store, event, source, live
        self.url_hash = stable_id(source.get("url") or source.get("endpoint"))

    def current(self):
        _, source, job = self.store.require_job(self.event)
        if source.get("source_config_revision") != self.source.get("source_config_revision"):
            raise InvalidRecord("web_ai_source_changed")
        if stable_id(source.get("url") or source.get("endpoint")) != self.url_hash:
            raise InvalidRecord("web_ai_source_changed")
        return source, job

    def reserve(self):
        tenant = self.event["tenant_id"]
        if self.live:
            authorize_live(tenant)
            if any(key.casefold() not in {"user-agent", "accept", "accept-language", "accept-encoding"} for key in (self.source.get("headers") or {})):
                raise InvalidRecord("web_ai_private_source_blocked")
            parts = urlsplit(self.source.get("url") or self.source.get("endpoint"))
            if parts.username or parts.password:
                raise InvalidRecord("web_ai_private_source_blocked")
        _, job = self.current()
        generation, values = generation_condition(job.get("source_generation"))
        values.update({":source": self.event["source_id"], ":limit": 2, ":one": 1,
                       ":queued": "queued", ":extracting": "extracting"})
        try:
            self.store.control.update_item(
                Key={"pk": pk(tenant), "sk": "JOB#" + identifier(self.event["job_id"])},
                UpdateExpression="ADD web_ai_calls :one",
                ConditionExpression="attribute_exists(pk) AND source_id = :source AND " + generation
                + " AND #status IN (:queued, :extracting) AND (attribute_not_exists(web_ai_calls) OR web_ai_calls < :limit)",
                ExpressionAttributeNames={"#status": "status"}, ExpressionAttributeValues=values,
            )
            now = datetime.now(UTC)
            period = stable_id(os.environ.get("WEB_AI_USAGE_APPROVAL_REF"), os.environ.get("WEB_AI_APPROVED_UNTIL")) if self.live else now.date().isoformat()
            self.store.control.update_item(
                Key={"pk": pk(tenant), "sk": "AI_USAGE#" + period},
                UpdateExpression="SET expires_at = :expires ADD web_ai_calls :one",
                ConditionExpression="attribute_not_exists(web_ai_calls) OR web_ai_calls < :limit",
                ExpressionAttributeValues={":one": 1, ":limit": 20, ":expires": int((now + timedelta(days=2)).timestamp())},
            )
        except self.store.control.meta.client.exceptions.ConditionalCheckFailedException:
            self.current()
            raise InvalidRecord("web_ai_budget_exhausted") from None
        # Reservations are never refunded: a crash must not allow repeated paid calls.
        self.current()

    def cached(self):
        cache = self.source.get("web_ai_rules") or {}
        if not isinstance(cache, dict):
            return {}
        try:
            expires_at = float(cache.get("expires_at", 0))
        except (TypeError, ValueError):
            return {}
        if (cache.get("url_hash") == self.url_hash and cache.get("schema_version") == 1
                and expires_at > datetime.now(UTC).timestamp()):
            return cache.get("profiles") if isinstance(cache.get("profiles"), dict) else {}
        return {}

    def save(self, profiles):
        source, _ = self.current()
        generation, values = generation_condition(source.get("source_generation"))
        values.update({":enabled": True, ":cache": {
            "schema_version": 1, "url_hash": self.url_hash, "profiles": profiles,
            "expires_at": int((datetime.now(UTC) + timedelta(hours=24)).timestamp()),
        }})
        condition = "attribute_exists(pk) AND enabled = :enabled AND " + generation
        if source.get("source_config_revision"):
            condition += " AND source_config_revision = :revision"
            values[":revision"] = source["source_config_revision"]
        else:
            condition += " AND attribute_not_exists(source_config_revision)"
        try:
            self.store.control.update_item(
                Key={"pk": pk(self.event["tenant_id"]), "sk": "SOURCE#" + identifier(self.event["source_id"])},
                UpdateExpression="SET web_ai_rules = :cache", ConditionExpression=condition,
                ExpressionAttributeValues=values,
            )
        except self.store.control.meta.client.exceptions.ConditionalCheckFailedException:
            raise InvalidRecord("web_ai_source_changed") from None


def build_extractor(store, event, source, client=None):
    if client is None and os.environ.get("WEB_AI_MODE", "off") != "gemini":
        return None
    state = AIState(store, event, source, live=client is None)
    return AIExtractor(client or LazyGemini(), state.reserve, cached=state.cached(), save=state.save)
