import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from marketdata.contracts import InvalidRecord, iso
from scripts.demo_source_pipeline import local_pipeline
from services.ingestion.fetch_errors import RetryableFetchError
from services.ingestion.storefront import discover
from services.ingestion.web_worker import collect_html_rows
from tests.conftest import NOW

HOME = "https://phuchy.vn/"
HOMEPAGE = Path("tests/fixtures/phuc_hy_homepage.html").read_bytes()
DETAIL = Path("tests/fixtures/product_metadata.html").read_bytes()
# Fixed fixtures for the same ten SKUs as the reference CSV, not current quotations.
PRODUCTS = [
    ("EXCEL-A4-70GSM", 68000), ("TL-027-XANH", 6000), ("FO-BC017", 95000),
    ("DOUBLE-A-A4-70GSM", 85000), ("FO-ST02", 28000), ("FO-DB01-XANH", 45000),
    ("BKT-10", 40000), ("GEL-072-XANH", 11600), ("FO-CH01", 3300), ("FO-STS02", 3600),
]


def product_urls(data):
    return discover(data, HOME).products()


def fixture_feeds():
    return {HOME: HOMEPAGE, **{
        url: DETAIL.replace(b"DOUBLE-A-A4-70GSM", sku.encode()).replace(b"85000", str(price).encode())
        for url, (sku, price) in zip(product_urls(HOMEPAGE), PRODUCTS, strict=True)
    }}


def test_homepage_collects_first_ten_unique_product_cards_in_page_order():
    feeds, calls = fixture_feeds(), []

    def fetch(url, headers=None):
        calls.append((url, headers))
        return feeds[url]

    rows = collect_html_rows(HOME, observed_at=iso(NOW), price_basis="tax_included", fetcher=fetch)
    assert calls == [(url, None) for url in [HOME, *product_urls(HOMEPAGE)]]
    assert [(row["source_sku"], int(row["price"])) for row in rows] == PRODUCTS
    assert all(row["observed_at"] == iso(NOW) and row["price_basis"] == "tax_included" for row in rows)


def test_homepage_discovery_deduplicates_queries_and_rejects_unrelated_destinations():
    links = b'<h3 class="other"><a href="/products/not-a-card">Ignore</a></h3>'
    for href in (
        "https://outside.example/products/a", "//127.0.0.1/products/a", "http://phuchy.vn/products/a",
        "https://phuchy.vn.evil.example/products/a", "https://user@phuchy.vn/products/a",
        "https://phuchy.vn:444/products/a", "/products/../account", "/collections/all",
        "/products/paper?tracking=one", "https://phuchy.vn/products/paper/#details",
    ):
        links += f'<h3 class="pro-name"><a href="{href}">Product</a></h3>'.encode()
    assert product_urls(links) == [HOME + "products/paper"]


@pytest.mark.parametrize("url", [
    "http://phuchy.vn/", "https://phuchy.vn/products/paper", "https://another.example/",
    "https://phuchy.vn.evil.example/", "https://phuchy.vn/?search=paper",
])
def test_readable_pages_do_not_trigger_extra_requests(url):
    calls = []

    def fetch(target, headers=None):
        calls.append(target)
        return DETAIL

    assert len(collect_html_rows(url, fetcher=fetch)) == 1
    assert calls == [url]


def test_homepage_budget_stops_before_requesting_more_products(monkeypatch):
    feeds, calls = fixture_feeds(), []
    clock = iter([0, 1, 2, 21])
    monkeypatch.setattr("services.ingestion.web_worker.monotonic", lambda: next(clock))

    def fetch(url, headers=None):
        calls.append(url)
        return feeds[url]

    with pytest.raises(RetryableFetchError, match="web_homepage_timeout"):
        collect_html_rows(HOME, fetcher=fetch)
    assert calls == [HOME, product_urls(HOMEPAGE)[0]]


def test_real_fetcher_contract_uses_short_timeouts_for_detail_pages(monkeypatch):
    feeds, calls = fixture_feeds(), []

    def fetch(url, headers=None, timeout=10):
        calls.append(timeout)
        return feeds[url]

    monkeypatch.setattr("services.ingestion.web_worker.default_fetcher", fetch)
    assert len(collect_html_rows(HOME)) == 10
    assert calls == [10, *([3] * 10)]


def test_no_product_cards_or_oversized_homepage_does_not_trigger_detail_requests():
    for data, error in ((b"<h1>No products</h1>", "html_products_not_found"), (b"x" * (10 * 1024 * 1024 + 1), "html_response_exceeds_10_mib")):
        calls = []

        def fetch(url, headers=None, calls=calls, data=data):
            calls.append(url)
            return data

        with pytest.raises(InvalidRecord, match=error):
            collect_html_rows(HOME, fetcher=fetch)
        assert calls == [HOME]


def test_homepage_url_to_ten_product_comparison_and_snapshot_replay():
    with local_pipeline() as p:
        p.feeds.update(fixture_feeds())
        for kind, source, url in (("web", "phuc-hy", HOME), ("api", "internal", "https://internal.example.invalid/prices")):
            assert p.api("POST", "/sources", body={
                "source_id": source, "kind": kind, "url": url,
                **({"price_basis": "tax_included"} if kind == "web" else {}),
            })[0] == 201
        p.feeds["https://internal.example.invalid/prices"] = json.dumps([
            {"source_sku": sku, "price": price + 1000, "currency": "VND",
             "observed_at": iso(p.now - timedelta(minutes=5)), "price_basis": "tax_included"}
            for sku, price in PRODUCTS
        ]).encode()
        assert p.dispatch() == {"sent": 2}
        for kind in ("web", "api"):
            assert not p.run_sources(kind)["batchItemFailures"]
        assert len(p.fetch_calls) == 12
        # Replay must use the saved snapshot even if the homepage is now unavailable.
        p.feeds[HOME] = RetryableFetchError("web_http_retryable_503")
        p.dispatch()
        assert not p.run_sources("web")["batchItemFailures"]
        assert len(p.fetch_calls) == 12
        p.advance(hours=1)
        p.normalize()
        pending = p.store.receipts("tenant-a", status="pending")
        assert len(pending) == 20
        for index, (sku, _) in enumerate(PRODUCTS):
            product_id = f"product-{index}"
            p.store.create_product("tenant-a", product_id, sku, {})
            for row in pending:
                if row["source_sku"] == sku:
                    p.store.approve_pending("tenant-a", row["observation_id"], product_id, now=p.now)
        rows = p.matrix(reference_source="internal")["data"]
        assert len(rows) == 20
        competitor = [row for row in rows if row["source_id"] == "phuc-hy"]
        assert len(competitor) == 10
        expected = {
            f"product-{index}": (Decimal(-1000) / Decimal(price + 1000) * 100).quantize(Decimal("0.01"))
            for index, (_, price) in enumerate(PRODUCTS)
        }
        assert {row["product_id"]: Decimal(str(row["change_percent"])) for row in competitor} == expected
        assert p.api("GET", "/matrix", user="viewer-b")[1]["data"] == []


@pytest.mark.parametrize("error", [RetryableFetchError("web_http_retryable_503"), InvalidRecord("invalid_html_price")])
def test_failed_detail_page_cannot_emit_or_snapshot_a_partial_homepage(error):
    with local_pipeline() as p:
        p.feeds.update(fixture_feeds())
        p.feeds[product_urls(HOMEPAGE)[4]] = error
        assert p.api("POST", "/sources", body={
            "source_id": "phuc-hy", "kind": "web", "url": HOME, "price_basis": "tax_included",
        })[0] == 201
        p.dispatch()
        result = p.run_sources("web")
        job = p.store.get("tenant-a", "JOB#" + result["bodies"][0]["job_id"])
        assert "source_snapshot" not in job and not job.get("row_count")
        assert p.normalize() == 0
        assert not p.s3.list_objects_v2(Bucket=p.bucket).get("Contents")
