import copy
import io
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from marketdata.contracts import InvalidRecord, iso
from scripts.demo_source_pipeline import local_pipeline
from services.ingestion.ai_client import GeminiRulesClient
from services.ingestion.ai_rules import AIExtractor, Document, extract, validate_rules
from services.ingestion.ai_state import AIState, authorize_live, build_extractor
from services.ingestion.web_worker import _normalize_price, collect_html_rows, process_event
from tests.conftest import NOW

URL = "https://unfamiliar.example.invalid/"
RULES = {"kind": "products", "card": {"tag": "article", "class": "tile"}, "fields": {
    "source_sku": {"tag": "span", "class": "ref", "prefix": "SKU: "},
    "source_name": {"tag": "h4", "class": "label"},
    "price": {"tag": "span", "class": "selling"},
}}
LINKS = {"kind": "links", "link": {"tag": "a", "class": "book", "attribute": "href"}}


def page(sku="ABC", price="85.000", cls="tile"):
    return f'<article class="{cls}"><span class="ref">SKU: {sku}</span><h4 class="label">Paper A4</h4><span class="selling">{price} ₫</span></article>'.encode()


class FakeAI:
    def __init__(self, *rules):
        self.rules = list(rules or [RULES])
        self.prompts = []

    def suggest(self, prompt):
        self.prompts.append(prompt)
        if not self.rules:
            raise AssertionError("unexpected_model_call")
        return copy.deepcopy(self.rules.pop(0))


def create(p, source="web", url=URL, user="admin-a"):
    status, response = p.api("POST", "/sources", user=user, body={
        "source_id": source, "kind": "web", "url": url, "price_basis": "tax_included", "collect_now": True,
    })
    assert status == 201
    tenant = "tenant-b" if user == "admin-b" else "tenant-a"
    return {"schema_version": 1, "tenant_id": tenant, "source_id": source,
            "job_id": response["job_id"], "kind": "web", "scheduled_at": iso(p.now)}


def run(p, event, ai):
    process_event(event, p.store, p.sqs, p.queues["transform"], fetcher=p.fetch, ai_client=ai)
    return p.store.get(event["tenant_id"], "JOB#" + event["job_id"])


def test_ai_fallback_preserves_real_evidence_and_names_through_catalog_and_snapshot_replay():
    with local_pipeline() as p:
        p.feeds[URL] = page()
        event, ai = create(p), FakeAI()
        job = run(p, event, ai)
        assert job["row_count"] == 1 and job["web_ai_calls"] == 1
        assert p.store.get("tenant-a", "SOURCE#web")["web_ai_rules"]["profiles"]["root"] == RULES
        p.feeds.clear()
        run(p, event, ai)
        assert len(ai.prompts) == 1
        p.normalize()
        row = p.api("GET", "/products", query={"match_status": "pending"})[1]["data"][0]
        assert row["source_name"] == "Paper A4" and row["price"] == 85000
        assert row["extraction_method"] == "ai_rules" and row["source_url"] == URL.rstrip("/")
        assert p.api("GET", "/products", user="viewer-b")[1]["data"] == []
        assert "web_ai_rules" not in p.api("GET", "/sources")[1]["data"][0]


def test_next_job_reuses_cached_rules_with_new_prices_without_model_call():
    with local_pipeline() as p:
        p.feeds[URL] = page()
        ai, event = FakeAI(), create(p)
        run(p, event, ai)
        p.advance(hours=1)
        status, response = p.api("PATCH", "/sources/web", body={"collect_now": True})
        assert status == 200
        p.feeds[URL] = page(price="90.000")
        second = {**event, "job_id": response["job_id"], "scheduled_at": iso(p.now)}
        job = run(p, second, ai)
        assert "web_ai_calls" not in job and len(ai.prompts) == 1
        p.normalize()
        assert {int(row["price"]) for row in p.store.receipts("tenant-a", status="pending")} == {85000, 90000}


def test_unknown_listing_and_detail_template_need_at_most_two_model_calls():
    ai = FakeAI(LINKS, RULES)
    extractor = AIExtractor(ai, lambda: None)
    feeds = {URL: ''.join(f'<a class="book" href="/book-{i}">Book</a>' for i in range(12)).encode()}
    feeds.update({URL + f"book-{i}": page(sku=f"SKU-{i}") for i in range(10)})
    rows = collect_html_rows(URL, fetcher=lambda url, **kw: feeds[url], ai_extractor=extractor)
    assert len(rows) == 10 and len(ai.prompts) == 2
    assert all(row["extraction_method"] == "ai_rules" for row in rows)


def test_regular_parser_is_always_preferred_without_spending_ai_budget():
    ai = FakeAI()
    rows = collect_html_rows(URL, fetcher=lambda *a, **kw: b'<div data-sku="A" data-price="100"></div>',
                             ai_extractor=AIExtractor(ai, lambda: pytest.fail("budget_reserved")))
    assert len(rows) == 1 and ai.prompts == []


def test_a_second_html_layout_can_read_explicit_sku_attributes_and_gbp():
    data = b'<li class="product"><h2 class="title">Book</h2><a class="buy" data-product_sku="9780000000000">Buy</a><span class="amount">\xc2\xa363.00</span></li>'
    rules = {"kind": "products", "card": {"tag": "li", "class": "product"}, "fields": {
        "source_sku": {"tag": "a", "class": "buy", "attribute": "data-product_sku"},
        "source_name": {"tag": "h2", "class": "title"}, "price": {"tag": "span", "class": "amount"},
    }}
    rows = collect_html_rows(URL, fetcher=lambda *a, **kw: data, ai_extractor=AIExtractor(FakeAI(rules), lambda: None))
    assert rows[0]["price"] == "63.00" and rows[0]["currency"] == "GBP"


def test_cached_rules_are_isolated_by_tenant_and_source_lifetime():
    with local_pipeline() as p:
        p.feeds[URL] = page()
        ai = FakeAI(RULES, RULES, RULES)
        event = create(p)
        run(p, event, ai)
        other = create(p, user="admin-b")
        run(p, other, ai)
        assert len(ai.prompts) == 2
        assert p.api("DELETE", "/sources/web")[0] == 200
        replacement = create(p)
        run(p, replacement, ai)
        assert len(ai.prompts) == 3
        calls = len(ai.prompts)
        run(p, event, ai)
        assert len(ai.prompts) == calls


def test_changed_layout_relearns_but_does_not_reuse_a_stale_price():
    with local_pipeline() as p:
        p.feeds[URL] = page()
        ai, event = FakeAI(RULES), create(p)
        run(p, event, ai)
        changed = copy.deepcopy(RULES)
        changed["card"]["class"] = "changed"
        ai.rules.append(changed)
        p.advance(hours=1)
        _, response = p.api("PATCH", "/sources/web", body={"collect_now": True})
        p.feeds[URL] = page(price="99.000", cls="changed")
        run(p, {**event, "job_id": response["job_id"], "scheduled_at": iso(p.now)}, ai)
        assert len(ai.prompts) == 2
        p.normalize()
        assert {int(row["price"]) for row in p.store.receipts("tenant-a", status="pending")} == {85000, 99000}


def test_authenticated_sources_cannot_send_their_html_to_live_ai(monkeypatch):
    with local_pipeline() as p:
        event = create(p)
        p.api("PATCH", "/sources/web", body={"headers": {"Authorization": "FAKE_ONLY_NOT_A_TOKEN"}})
        source = p.store.get("tenant-a", "SOURCE#web")
        monkeypatch.setattr("services.ingestion.ai_state.authorize_live", lambda tenant: None)
        with pytest.raises(InvalidRecord, match="web_ai_private_source_blocked"):
            AIState(p.store, event, source, live=True).reserve()
        assert "web_ai_calls" not in p.store.get("tenant-a", "JOB#" + event["job_id"])


@pytest.mark.parametrize("bad", [
    {"kind": "products", "rows": [{"price": 1}]},
    {"kind": "links", "link": {"tag": "a", "attribute": "href", "code": "execute()"}},
    {"kind": "products", "card": {"tag": "body"}, "fields": RULES["fields"]},
    {"kind": "links", "link": {"tag": "script", "attribute": "href"}},
    {"kind": "links", "link": {"tag": "a", "attribute": {"value": "href"}}},
])
def test_model_cannot_invent_values_execute_code_or_select_entire_page(bad):
    with pytest.raises(InvalidRecord, match="web_ai_invalid_rules"):
        validate_rules(bad)


@pytest.mark.parametrize("payload", [
    page().replace(b'<span class="ref">SKU: ABC</span>', b''),
    page().replace(b'85.000', b'85.000 90.000'),
    page().replace(b'<span class="selling">', b'<del><span class="selling">').replace(b'</span></article>', b'</span></del></article>'),
    page().replace('₫'.encode(), b'$'),
    page().replace(b'SKU: ABC', b'SKU: '),
])
def test_missing_ambiguous_and_struck_out_prices_never_become_rows(payload):
    with pytest.raises(InvalidRecord):
        extract(Document(payload, URL), RULES, URL, _normalize_price, iso(NOW))


def test_external_private_and_script_links_are_not_followed():
    data = b'''<a class="book" href="http://127.0.0.1/private">Bad</a>
    <a class="book" href="https://other.example/book">Bad</a>
    <a class="book" href="javascript:execute()">Bad</a>
    <a class="book" href="/real?session=fake#part">Real</a>'''
    assert extract(Document(data, URL), LINKS, URL, _normalize_price, iso(NOW)) == ("links", [URL + "real"])


def test_ai_context_excludes_scripts_forms_headers_cookies_and_signed_query_values():
    data = b'''<script>FAKE_SCRIPT_CREDENTIAL</script><form><input value="FAKE_PASSWORD"></form>
    <meta name="csrf-token" content="FAKE_CSRF">
    <a class="book" href="/real?signature=FAKE_SIGNATURE">Real</a>'''
    context = Document(data, URL).context()
    assert all(value not in context for value in ("FAKE_SCRIPT_CREDENTIAL", "FAKE_PASSWORD", "FAKE_CSRF", "FAKE_SIGNATURE"))
    assert URL + "real" in context


def test_explicit_currency_cannot_contradict_the_displayed_price():
    rules = copy.deepcopy(RULES)
    rules["fields"]["currency"] = {"tag": "span", "class": "currency"}
    data = page().replace(b'</article>', b'<span class="currency">USD</span></article>')
    with pytest.raises(InvalidRecord, match="web_ai_currency_missing"):
        extract(Document(data, URL), rules, URL, _normalize_price, iso(NOW))


def test_failure_after_a_valid_detail_cannot_publish_partial_rows_or_profiles():
    with local_pipeline() as p:
        p.feeds[URL] = b'<a class="book" href="/one">One</a><a class="book" href="/two">Two</a>'
        p.feeds[URL + "one"], p.feeds[URL + "two"] = page(), b'<article class="tile">No SKU/price</article>'
        event, ai = create(p), FakeAI(LINKS, RULES)
        job = run(p, event, ai)
        assert job["status"] == "failed" and job["error"] == "web_ai_budget_exhausted"
        assert len(ai.prompts) == 2 and "source_snapshot" not in job
        assert "web_ai_rules" not in p.store.get("tenant-a", "SOURCE#web")
        assert p.normalize() == 0


def test_retries_cannot_reset_durable_per_job_reservations():
    with local_pipeline() as p:
        event = create(p)
        source = p.store.get("tenant-a", "SOURCE#web")
        AIState(p.store, event, source, live=False).reserve()
        AIState(p.store, event, source, live=False).reserve()
        with pytest.raises(InvalidRecord, match="web_ai_budget_exhausted"):
            AIState(p.store, event, source, live=False).reserve()
        assert p.store.get("tenant-a", "JOB#" + event["job_id"])["web_ai_calls"] == 2


def test_tenant_call_cap_blocks_calls_even_from_a_new_job():
    with local_pipeline() as p:
        event = create(p)
        p.store.put("tenant-a", "AI_USAGE#" + datetime.now(UTC).date().isoformat(), {"web_ai_calls": 20})
        ai = FakeAI()
        # No HTTP fixture registered here; test reservations directly before calling a provider.
        state = AIState(p.store, event, p.store.get("tenant-a", "SOURCE#web"), live=False)
        with pytest.raises(InvalidRecord, match="web_ai_budget_exhausted"):
            state.reserve()
        assert ai.prompts == []


def test_default_mode_does_not_inherit_reporting_ai_credentials(monkeypatch):
    monkeypatch.delenv("WEB_AI_MODE", raising=False)
    monkeypatch.setenv("GEMINI_SECRET_ARN", "fake-reporting-secret")
    assert build_extractor(None, {}, {}) is None


def test_live_policy_requires_approval_allowlist_and_a_short_expiry(monkeypatch):
    for key in ("WEB_AI_USAGE_APPROVAL_REF", "WEB_AI_APPROVED_UNTIL", "WEB_AI_TENANT_ALLOWLIST"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(InvalidRecord, match="web_ai_approval_required"):
        authorize_live("tenant-a")
    monkeypatch.setenv("WEB_AI_USAGE_APPROVAL_REF", "fixture-approval-not-real")
    monkeypatch.setenv("WEB_AI_TENANT_ALLOWLIST", "tenant-a")
    monkeypatch.setenv("WEB_AI_APPROVED_UNTIL", iso(datetime.now(UTC) + timedelta(hours=1)))
    authorize_live("tenant-a")
    with pytest.raises(InvalidRecord, match="web_ai_approval_required"):
        authorize_live("tenant-b")
    monkeypatch.setenv("WEB_AI_APPROVED_UNTIL", iso(datetime.now(UTC) + timedelta(days=2)))
    with pytest.raises(InvalidRecord, match="web_ai_approval_required"):
        authorize_live("tenant-a")


def test_source_configuration_change_cannot_persist_old_ai_rules():
    with local_pipeline() as p:
        event = create(p)
        state = AIState(p.store, event, p.store.get("tenant-a", "SOURCE#web"), live=False)
        p.api("PATCH", "/sources/web", body={"url": "https://changed.example.invalid/"})
        with pytest.raises(InvalidRecord, match="web_ai_source_changed"):
            state.save({"root": RULES})


def test_model_transport_is_bounded_json_and_errors_do_not_expose_secrets(monkeypatch):
    monkeypatch.setenv("WEB_AI_USAGE_APPROVAL_REF", "fixture-only")
    monkeypatch.setenv("WEB_AI_API_KEY", "FAKE_TEST_KEY_NO_ACCESS")
    monkeypatch.setenv("WEB_AI_MODEL", "fixture-model")
    response = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(RULES)}]}}]}
    opener = Mock()
    opener.open.return_value = io.BytesIO(json.dumps(response).encode())
    monkeypatch.setattr("urllib.request.build_opener", lambda *a: opener)
    assert GeminiRulesClient().suggest("public snippet") == RULES
    request = opener.open.call_args.args[0]
    assert request.full_url.endswith("fixture-model:generateContent")
    assert b'FAKE_TEST_KEY_NO_ACCESS' not in request.data
    assert opener.open.call_args.kwargs["timeout"] == 5
    opener.open.side_effect = RuntimeError("FAKE_TEST_KEY_NO_ACCESS")
    with pytest.raises(InvalidRecord, match="^web_ai_unavailable$"):
        GeminiRulesClient().suggest("public snippet")
