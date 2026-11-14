"""Independent storefront structures with no live network or AWS calls."""

import json

import pytest

from marketdata.contracts import InvalidRecord, iso
from scripts.demo_source_pipeline import local_pipeline
from services.ingestion.storefront import discover
from services.ingestion.web_worker import collect_html_rows, parse_html_payload
from tests.conftest import NOW


def product_page(sku, price=12000):
    return ('<script type="application/ld+json">' + json.dumps({
        "@type": "Product", "sku": sku,
        "offers": {"@type": "Offer", "price": price, "priceCurrency": "VND"},
    }) + '</script>').encode()


def feed_fixture(platform="Haravan", currency="VND"):
    if platform == "Haravan":
        script = f"Haravan.shop = 'fixture.myharavan.com'; window.HaravanAnalytics.meta.currency = '{currency}';"
        sections = '<section data-handle="new-products">Loading...</section>'
        endpoint = "/collections/new-products/products.json?page=1&limit=10"
    else:
        script = f'Shopify.shop = "fixture.myshopify.com"; ShopifyAnalytics.meta = {{"currency":"{currency}"}}; Shopify.currency = {{"active":"EUR"}};'
        sections = ""
        endpoint = "/products.json?page=1&limit=10"
    html = (f"<script>{script}</script>" + sections).encode()
    products = [{"variants": [
        {"sku": f"SKU-{i}-BLUE", "price": "12000", "available": True},
        {"sku": f"SKU-{i}-RED", "price": "14000", "available": True},
        {"sku": f"SKU-{i}-UNAVAILABLE", "price": "15000", "available": False},
    ]} for i in range(10)]
    return html, endpoint, {"products": products}


@pytest.mark.parametrize("base,path", [
    ("https://stationery.example/", "/products/pen"),
    ("https://woo.example/catalog/", "/product/paper"),
    ("https://vietnam.example/", "/san-pham/but-bi.html"),
    ("https://another.example/collections/office", "/collections/office/products/pen"),
])
def test_different_websites_can_discover_product_pages(base, path):
    from urllib.parse import urljoin

    target = urljoin(base, path)
    feeds = {base: f'<a href="{path}">Product</a><a href="{path}?tracking=1">Repeat</a>'.encode(),
             target: product_page("PEN")}
    calls = []

    def fetch(url, headers=None):
        calls.append(url)
        return feeds[url]

    rows = collect_html_rows(base, fetcher=fetch)
    assert rows[0]["source_sku"] == "PEN"
    assert calls == [base, target]


def test_schema_item_list_supports_product_urls_without_standard_path():
    base = "https://shop.example/"
    html = ('<script type="application/ld+json">' + json.dumps({
        "@type": "ItemList", "itemListElement": [
            {"@type": "ListItem", "item": {"@type": "Product", "url": "/paper.html"}},
            {"@type": "ListItem", "url": "https://external.example/paper.html"},
        ],
    }) + '</script>').encode()
    assert discover(html, base).products() == [base + "paper.html"]


def test_discovery_is_bounded_same_origin_and_ignores_untrusted_base_tag():
    base = "https://shop.example/"
    html = '<base href="http://127.0.0.1/">'
    for href in (
        "//127.0.0.1/products/a", "https://external.example/products/a", "http://shop.example/products/a",
        "https://user@shop.example/products/a", "https://shop.example:444/products/a",
        "/products/%2e%2e", "javascript:alert(1)", "file:///products/a",
    ):
        html += f'<a href="{href}">Ignore</a>'
    html += ''.join(f'<a href="/products/item-{i}">Product</a>' for i in range(20))
    assert discover(html.encode(), base).products() == [base + f"products/item-{i}" for i in range(10)]


@pytest.mark.parametrize("platform,currency", [("Haravan", "VND"), ("Shopify", "USD")])
def test_public_json_feed_preserves_each_variant_sku_price_and_currency(platform, currency):
    base = "https://generic-shop.example/"
    html, path, document = feed_fixture(platform, currency)
    feeds = {base: html, base.rstrip("/") + path: json.dumps(document).encode()}
    calls = []

    def fetch(url, headers=None):
        calls.append(url)
        return feeds[url]

    rows = collect_html_rows(base, fetcher=fetch, observed_at=iso(NOW), price_basis="tax_excluded")
    assert len(rows) == 20
    assert rows[:2] == [
        {"source_sku": "SKU-0-BLUE", "price": "12000", "currency": currency,
         "observed_at": iso(NOW), "price_basis": "tax_excluded"},
        {"source_sku": "SKU-0-RED", "price": "14000", "currency": currency,
         "observed_at": iso(NOW), "price_basis": "tax_excluded"},
    ]
    assert calls == list(feeds)


@pytest.mark.parametrize("mutation,error", [
    (lambda d: d["products"][1]["variants"][0].update(sku=""), "html_missing_product_sku"),
    (lambda d: d["products"][1]["variants"][0].update(sku="SKU-0-BLUE", price="1"), "ambiguous_html_product"),
    (lambda d: d["products"][1]["variants"][0].update(price=True), "invalid_html_price"),
    (lambda d: d.update(products={}), "invalid_html_storefront_feed"),
])
def test_invalid_feed_never_creates_a_partial_snapshot(mutation, error):
    html, path, document = feed_fixture()
    mutation(document)
    base = "https://shop.example.invalid/"
    with local_pipeline() as p:
        p.feeds.update({base: html, base.rstrip("/") + path: json.dumps(document).encode()})
        assert p.api("POST", "/sources", body={"source_id": "shop", "kind": "web", "url": base})[0] == 201
        p.dispatch()
        result = p.run_sources("web")
        job = p.store.get("tenant-a", "JOB#" + result["bodies"][0]["job_id"])
        assert job["status"] == "failed" and job["error"] == error
        assert "source_snapshot" not in job
        assert p.normalize() == 0


def test_platform_feed_requires_explicit_currency_and_never_guesses_vnd():
    html, _, _ = feed_fixture()
    html = html.replace(b"meta.currency", b"meta.missing")
    with pytest.raises(InvalidRecord, match="html_storefront_currency_missing"):
        collect_html_rows("https://shop.example/", fetcher=lambda *a, **kw: html)


def test_arbitrary_javascript_urls_and_login_pages_are_not_followed():
    calls = []

    def fetch(url, headers=None):
        calls.append(url)
        return b'<script>fetch("http://127.0.0.1/admin")</script><h1>Login required</h1>'

    with pytest.raises(InvalidRecord, match="html_products_not_found"):
        collect_html_rows("https://shop.example/", fetcher=fetch)
    assert calls == ["https://shop.example/"]


def test_detail_url_reads_its_own_feed_instead_of_recommended_products():
    url = "https://shop.example/products/pen"
    html, _, document = feed_fixture()
    html += b'<a href="/products/unrelated">Recommended product</a>'
    feeds = {url: html, url + ".json": json.dumps({"product": document["products"][0]}).encode()}
    calls = []

    def fetch(target, headers=None):
        calls.append(target)
        return feeds[target]

    assert len(collect_html_rows(url, fetcher=fetch)) == 2
    assert calls == [url, url + ".json"]


def test_shopify_presentment_currency_alone_cannot_label_shop_feed_prices():
    html, _, _ = feed_fixture("Shopify", "USD")
    html = html.replace(b'ShopifyAnalytics.meta = {"currency":"USD"};', b'')
    with pytest.raises(InvalidRecord, match="html_storefront_currency_missing"):
        collect_html_rows("https://shop.example/", fetcher=lambda *a, **kw: html)


def test_inline_microdata_and_woocommerce_style_price():
    html = b'''<div itemscope itemtype="https://schema.org/Product">
      <span itemprop="sku">PAPER</span>
      <div itemprop="offers" itemscope itemtype="https://schema.org/Offer">
        <span itemprop="price" content="85000"><b>85.000</b></span>
        <meta itemprop="priceCurrency" content="VND">
      </div></div>'''
    assert parse_html_payload(html)[0]["source_sku"] == "PAPER"
    assert parse_html_payload(html)[0]["price"] == "85000"
    text_price = html.replace(b'content="85000"', b'')
    assert parse_html_payload(text_price)[0]["price"] == "85000"


def test_open_graph_requires_explicit_product_type_sku_and_currency():
    html = b'''<meta property="og:type" content="product">
      <meta property="product:retailer_item_id" content="PEN">
      <meta property="product:price:amount" content="6000">
      <meta property="product:price:currency" content="VND">'''
    assert parse_html_payload(html)[0]["source_sku"] == "PEN"
    with pytest.raises(InvalidRecord, match="empty_or_unparseable_html_table"):
        parse_html_payload(html.replace(b'content="product"', b'content="article"'))


def test_storefront_feed_snapshot_replays_without_new_requests_and_isolates_tenants():
    html, path, document = feed_fixture()
    base = "https://shop.example.invalid/"
    with local_pipeline() as p:
        p.feeds.update({base: html, base.rstrip("/") + path: json.dumps(document).encode()})
        p.api("POST", "/sources", body={"source_id": "shop", "kind": "web", "url": base,
                                          "price_basis": "tax_included", "collect_now": True})
        first = p.run_sources("web")
        assert first["received"] == 1 and not first["batchItemFailures"]
        calls = list(p.fetch_calls)
        p.feeds.clear()
        p.sqs.send_message(QueueUrl=p.queues["web"], MessageBody=json.dumps(first["bodies"][0]))
        replay = p.run_sources("web")
        assert replay["received"] == 1 and not replay["batchItemFailures"]
        assert p.fetch_calls == calls
        p.advance(hours=1)
        p.normalize()
        assert len(p.store.receipts("tenant-a", status="pending")) == 20
        assert not p.store.receipts("tenant-b", status="pending")


def test_platform_with_product_links_can_fall_back_when_first_detail_has_no_sku():
    base = "https://shop.example/"
    html, path, document = feed_fixture()
    html += b'<a href="/products/pen">Pen</a>'
    feeds = {base: html, base + "products/pen": b'<h1>Pen</h1>',
             base.rstrip("/") + path: json.dumps(document).encode()}
    calls = []

    def fetch(url, headers=None):
        calls.append(url)
        return feeds[url]

    assert len(collect_html_rows(base, fetcher=fetch)) == 20
    assert calls == list(feeds)
