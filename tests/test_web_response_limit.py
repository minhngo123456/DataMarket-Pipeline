"""Large storefront pages and bounded reads, without live network requests."""

import io
import json

import pytest

from marketdata.contracts import InvalidRecord
from services.ingestion import web_worker
from services.ingestion.collection import collection_error_code

TEN_MIB = 10 * 1024 * 1024
PRODUCT = b'<div data-sku="PAPER" data-price="85000" data-currency="VND"></div>'


def page(size):
    return PRODUCT + b" " * (size - len(PRODUCT))


@pytest.mark.parametrize("size", [2 * 1024 * 1024, TEN_MIB])
def test_large_html_up_to_ten_mib_still_extracts_prices(size):
    rows = web_worker.parse_html_payload(page(size))
    assert [(row["source_sku"], row["price"]) for row in rows] == [("PAPER", "85000")]


@pytest.mark.parametrize("size,accepted", [(TEN_MIB, True), (TEN_MIB + 1, False)])
def test_http_reader_accepts_limit_and_stops_reading_on_overflow(monkeypatch, size, accepted):
    class Response(io.BytesIO):
        read_sizes = []

        def read(self, requested=-1):
            self.read_sizes.append(requested)
            return super().read(requested)

    response = Response(page(size))
    monkeypatch.setattr(web_worker, "open_public", lambda *a, **kw: response)
    if accepted:
        assert len(web_worker.default_fetcher("https://fixture.example/")) == TEN_MIB
    else:
        with pytest.raises(InvalidRecord, match="html_response_exceeds_10_mib"):
            web_worker.default_fetcher("https://fixture.example/")
    assert response.read_sizes == [TEN_MIB + 1]


def test_new_size_error_is_preserved_in_source_health():
    assert collection_error_code("html_response_exceeds_10_mib", "web") == "html_response_exceeds_10_mib"


def test_injected_oversized_detail_is_rejected_before_parsing():
    home = "https://fixture.example/"
    feeds = {home: b'<a href="/products/paper">Paper</a>', home + "products/paper": page(TEN_MIB + 1)}
    with pytest.raises(InvalidRecord, match="html_response_exceeds_10_mib"):
        web_worker.collect_html_rows(home, fetcher=lambda url, **kw: feeds[url])


def test_storefront_json_over_old_limit_can_be_collected():
    home = "https://fixture.example/"
    feeds = {
        home: b'''<script>Haravan.shop = 'fixture.myharavan.com';
        HaravanAnalytics.meta.currency = 'VND';</script><section data-handle="office"></section>''',
        home + "collections/office/products.json?page=1&limit=10": json.dumps({
            "products": [{"body_html": "x" * (2 * 1024 * 1024),
                          "variants": [{"sku": "PAPER", "price": "85000", "available": True}]}],
        }).encode(),
    }
    rows = web_worker.collect_html_rows(home, fetcher=lambda url, **kw: feeds[url])
    assert rows[0]["source_sku"] == "PAPER"
