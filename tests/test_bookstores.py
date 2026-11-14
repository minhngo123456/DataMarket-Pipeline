"""Minimal public markup patterns; synthetic prices and no live network/AWS."""

import json

import pytest

from marketdata.contracts import InvalidRecord, iso
from scripts.demo_source_pipeline import local_pipeline
from services.ingestion import fahasa
from services.ingestion.fetch_errors import RetryableFetchError
from services.ingestion.storefront import discover
from services.ingestion.web_worker import collect_html_rows, parse_html_payload
from tests.conftest import NOW

FAHASA = "https://www.fahasa.com/"
KIMDONG = "https://nxbkimdong.com.vn/"


def homepage():
    config = [{"daily": {"label": "Daily books", "list": ",".join(str(i) for i in range(12))}}]
    return ('<script>var current_block_data_str = ' + json.dumps(json.dumps(config)) + ';\n'
            'var TABSLIDER_GET_URL = "https://external.example/ignored";</script>').encode()


def book(url, sku, price, *, fahasa_markup=False):
    product = {"@type": ["Book", "Product"], "url": url, "offers": {
        "@type": "Offer", "url": url, "price": price, "priceCurrency": "VND",
    }}
    script = ""
    if fahasa_markup:
        script = f"<script>var sku_product = '{sku}';</script>"
    else:
        product["sku"] = sku
    return (script + '<script type="application/ld+json">' + json.dumps({"@graph": product}) + '</script>'
            '<span class="old-price">200000</span>').encode()


def feeds():
    home = homepage()
    result = {FAHASA: home}
    products = []
    kimdong = ""
    for i in range(10):
        sku = f"89300000000{i:02d}"
        f_url, k_url = FAHASA + f"book-{i}.html", KIMDONG + f"sach-{i}"
        products.append({"product_id": str(i), "product_url": f_url, "price": "200000.0000"})
        result[f_url] = book(f_url, sku, 100000 + i, fahasa_markup=True)
        result[k_url] = book(k_url, sku, 110000 + i)
        kimdong += f'<a class="product-transition" href="/sach-{i}">Image</a>'
        kimdong += f'<a class="item-product-name" href="/sach-{i}">Book</a>'
    result[KIMDONG] = kimdong.encode()
    result[fahasa.homepage_feed(home, FAHASA)] = json.dumps(products).encode()
    return result


@pytest.mark.parametrize("home,requests", [(FAHASA, 12), (KIMDONG, 11)])
def test_each_homepage_collects_ten_actual_skus_and_exact_offer_prices(home, requests):
    source, calls = feeds(), []

    def fetch(url, headers=None):
        calls.append(url)
        return source[url]

    rows = collect_html_rows(home, fetcher=fetch, observed_at=iso(NOW), price_basis="tax_included")
    assert len(rows) == 10 and len(calls) == requests
    assert rows[0] == {"source_sku": "8930000000000", "price": "100000" if home == FAHASA else "110000",
                       "currency": "VND", "observed_at": iso(NOW), "price_basis": "tax_included"}
    assert all(row["source_sku"] != str(i) for i, row in enumerate(rows))


def test_fahasa_endpoint_uses_only_bounded_public_ids_not_a_script_provided_url():
    url = fahasa.homepage_feed(homepage(), FAHASA)
    assert url == FAHASA + "tabslider/index/getdata/?limit=10&list=0%2C1%2C2%2C3%2C4%2C5%2C6%2C7%2C8%2C9"
    with pytest.raises(InvalidRecord, match="invalid_fahasa_product_feed"):
        fahasa.homepage_feed(homepage(), "https://www.fahasa.com.evil.example/")
    broken = b'<script>var current_block_data_str = alert("execute");</script>'
    with pytest.raises(InvalidRecord, match="invalid_fahasa_product_feed"):
        fahasa.homepage_feed(broken, FAHASA)


@pytest.mark.parametrize("href", [
    "https://external.example/book.html", "http://www.fahasa.com/book.html",
    "https://user@www.fahasa.com/book.html", "https://www.fahasa.com:444/book.html",
    "/account", "/book/%2e%2e.html",
])
def test_fahasa_feed_cannot_expand_request_scope(href):
    with pytest.raises(InvalidRecord, match="invalid_fahasa_product_feed"):
        fahasa.product_urls(json.dumps([{"product_url": href}]).encode(), FAHASA)


def test_semantic_links_support_bare_book_paths_without_following_navigation():
    html = b'''<a href="/navigation">Ignore</a>
       <a class="item-product-name" href="https://external.example/book">Ignore</a>
       <a class="item-product-name" href="/account">Ignore</a>
       <a class="item-product-name" href="/sach-1?tracking=1">Book</a>
       <a class="item-product-name" href="/sach-1">Duplicate</a>
       <h2 class="product-name"><a href="/sach-2.html">Book</a></h2>'''
    assert discover(html, KIMDONG).products() == [KIMDONG + "sach-1", KIMDONG + "sach-2.html"]


def test_fahasa_metadata_never_uses_a_recommendations_price_or_guesses_currency():
    url = FAHASA + "book.html"
    html = book(url, "8930000000000", 100000, fahasa_markup=True)
    assert parse_html_payload(html, url=url)[0]["price"] == "100000"
    with pytest.raises(InvalidRecord, match="empty_or_unparseable_html_table"):
        parse_html_payload(html.replace(url.encode(), (FAHASA + "other.html").encode()), url=url)
    with pytest.raises(InvalidRecord, match="empty_or_unparseable_html_table"):
        parse_html_payload(html.replace(b'"priceCurrency": "VND"', b'"priceCurrency": null'), url=url)
    with pytest.raises(InvalidRecord, match="empty_or_unparseable_html_table"):
        parse_html_payload(html, url="https://unrelated.example/book.html")
    with pytest.raises(InvalidRecord, match="ambiguous_html_product"):
        parse_html_payload(html + b"<script>var sku_product = 'OTHER';</script>", url=url)


def test_both_bookstores_collect_compare_and_replay_in_tenant_isolated_local_pipeline():
    with local_pipeline() as p:
        p.feeds.update(feeds())
        for source, home in (("fahasa", FAHASA), ("kimdong", KIMDONG)):
            assert p.api("POST", "/sources", body={"source_id": source, "kind": "web", "url": home,
                                                    "price_basis": "tax_included", "collect_now": True})[0] == 201
        result = p.run_sources("web")
        assert result["received"] == 2 and not result["batchItemFailures"]
        calls = list(p.fetch_calls)
        p.feeds.clear()
        for body in result["bodies"]:
            p.sqs.send_message(QueueUrl=p.queues["web"], MessageBody=json.dumps(body))
        replay = p.run_sources("web")
        assert replay["received"] == 2 and not replay["batchItemFailures"]
        assert p.fetch_calls == calls
        p.advance(hours=1)
        p.normalize()
        pending = p.store.receipts("tenant-a", status="pending")
        assert len(pending) == 20
        for i in range(10):
            sku, product = f"89300000000{i:02d}", f"book-{i}"
            p.store.create_product("tenant-a", product, product, {})
            for row in pending:
                if row["source_sku"] == sku:
                    p.store.approve_pending("tenant-a", row["observation_id"], product, now=p.now)
        assert len(p.matrix(reference_source="kimdong")["data"]) == 20
        assert p.api("GET", "/matrix", user="viewer-b")[1]["data"] == []


def test_failed_fahasa_detail_does_not_publish_partial_collection():
    with local_pipeline() as p:
        p.feeds.update(feeds())
        p.feeds[FAHASA + "book-4.html"] = RetryableFetchError("web_http_retryable_503")
        p.api("POST", "/sources", body={"source_id": "fahasa", "kind": "web", "url": FAHASA,
                                          "collect_now": True})
        run = p.run_sources("web")
        job = p.store.get("tenant-a", "JOB#" + run["bodies"][0]["job_id"])
        assert "source_snapshot" not in job
        assert p.normalize() == 0
