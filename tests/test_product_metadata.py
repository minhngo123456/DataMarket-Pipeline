import json

from marketdata.product_metadata import product_metadata
from scripts.demo_source_pipeline import local_pipeline
from services.ingestion.storefront import parse_storefront_feed
from services.ingestion.web_worker import _normalize_price, parse_html_payload


def test_variant_feed_carries_name_variant_and_safe_detail_url():
    data = json.dumps({"products": [{"title": "Bút gel", "handle": "but-gel", "variants": [
        {"sku": "123", "title": "Xanh / Hộp 10", "price": "12000"},
    ]}]}).encode()
    row = parse_storefront_feed(data, "VND", _normalize_price, "https://shop.example/")[0]
    assert row["source_name"] == "Bút gel"
    assert row["source_variant"] == "Xanh / Hộp 10"
    assert row["source_url"] == "https://shop.example/products/but-gel"


def test_optional_metadata_is_bounded_and_urls_never_carry_credentials_or_queries():
    assert product_metadata({"source_name": "  Bút \n gel  ", "source_variant": "x" * 500,
                             "source_url": "https://shop.example/item?token=fake#section"}) == {
        "source_name": "Bút gel", "source_variant": "x" * 200, "source_url": "https://shop.example/item",
    }
    for url in ("javascript:alert(1)", "https://fake:fake@shop.example/", "file:///test"):
        assert "source_url" not in product_metadata({"source_url": url})


def test_product_name_does_not_make_duplicate_price_metadata_conflicting():
    product = {"@type": "Product", "sku": "PEN", "name": "Bút gel", "offers": {
        "@type": "Offer", "price": "12000", "priceCurrency": "VND",
    }}
    data = ('<script type="application/ld+json">' + json.dumps([product, product]) + '</script>').encode()
    rows = parse_html_payload(data, url="https://shop.example/pen?tracking=fake")
    assert len(rows) == 1
    assert rows[0]["source_name"] == "Bút gel"
    assert rows[0]["source_url"] == "https://shop.example/pen"


def test_names_survive_snapshot_normalizer_catalog_and_future_jobs_use_mapping():
    with local_pipeline() as p:
        url = "https://shop.example.invalid/products/pen"
        p.feeds[url] = b'<div data-sku="123" data-price="12000" data-name="Pen" data-variant="Blue"></div>'
        p.api("POST", "/sources", body={"source_id": "shop", "kind": "web", "url": url, "collect_now": True})
        assert not p.run_sources("web")["batchItemFailures"]
        p.normalize()
        status, body = p.api("GET", "/products", query={"match_status": "pending"})
        assert status == 200 and len(body["data"]) == 1
        row = body["data"][0]
        assert {key: row[key] for key in ("source_name", "source_variant", "source_url")} == {
            "source_name": "Pen", "source_variant": "Blue", "source_url": url,
        }
        assert p.api("GET", "/products", user="viewer-b")[1]["data"] == []
        p.store.create_product("tenant-a", "pen", "Pen Blue", {})
        p.store.approve_pending("tenant-a", row["observation_id"], "pen", now=p.now)
        p.advance(hours=1)
        p.api("PATCH", "/sources/shop", body={"collect_now": True})
        p.run_sources("web")
        p.normalize()
        assert p.store.receipts("tenant-a", status="pending") == []
        assert len(p.store.receipts("tenant-a", status="accepted")) == 2


def test_recollection_describes_old_pending_rows_without_changing_their_prices():
    with local_pipeline() as p:
        url = "https://shop.example.invalid/"
        p.feeds[url] = b'<div data-sku="123" data-price="12000"></div>'
        p.api("POST", "/sources", body={"source_id": "shop", "kind": "web", "url": url, "collect_now": True})
        p.run_sources("web")
        p.normalize()
        old = p.store.receipts("tenant-a", status="pending")[0]
        p.advance(hours=1)
        p.feeds[url] = b'<div data-sku="123" data-price="13000" data-name="Pen"></div>'
        p.api("PATCH", "/sources/shop", body={"collect_now": True})
        p.run_sources("web")
        p.normalize()
        rows = p.api("GET", "/products", query={"match_status": "all"})[1]["data"]
        assert len(rows) == 2 and all(row["source_name"] == "Pen" for row in rows)
        old_receipt = p.store.receipt("tenant-a", old["observation_id"])
        assert old_receipt["price"] == old["price"]
        assert "source_name" not in old_receipt
        newest = next(row for row in p.store.receipts("tenant-a", status="pending") if "source_name" in row)
        p.store.put("tenant-a", "RECEIPT#" + newest["observation_id"], {**newest, "source_generation": "another-lifetime"})
        rows = p.api("GET", "/products", query={"match_status": "all"})[1]["data"]
        previous = next(row for row in rows if row["observation_id"] == old["observation_id"])
        assert "source_name" not in previous
