import json
from decimal import Decimal
from pathlib import Path

import pytest

from marketdata.contracts import InvalidRecord, iso
from scripts.demo_source_pipeline import local_pipeline
from services.ingestion.web_worker import parse_html_payload
from tests.conftest import NOW

FIXTURE = Path("tests/fixtures/product_metadata.html").read_bytes().replace(b"\r\n", b"\n")


def jsonld(document):
    return ('<script type="application/ld+json">' + json.dumps(document) + '</script>').encode()


def product(sku="PAPER-01", price="85000", currency="VND"):
    return {"@type": "Product", "sku": sku, "offers": {
        "@type": "Offer", "price": price, "priceCurrency": currency,
    }}


def test_real_product_metadata_pattern_and_source_basis():
    rows = parse_html_payload(FIXTURE, observed_at=iso(NOW), price_basis="tax_excluded")
    assert rows == [{
        "source_sku": "DOUBLE-A-A4-70GSM", "price": "85000", "currency": "VND",
        "observed_at": iso(NOW), "price_basis": "tax_excluded",
    }]
    assert "price_basis" not in parse_html_payload(FIXTURE, observed_at=iso(NOW))[0]


@pytest.mark.parametrize("wrap", [
    lambda value: value,
    lambda value: [value],
    lambda value: {"@graph": [{"@type": "Organization"}, value]},
    lambda value: {"@type": "ItemList", "itemListElement": [{"@type": "ListItem", "item": value}]},
])
def test_jsonld_graphs_and_lists_preserve_explicit_currency(wrap):
    rows = parse_html_payload(jsonld(wrap(product(price="10.25", currency="USD"))))
    assert rows[0]["currency"] == "USD"
    assert rows[0]["price"] == "10.25"


def test_jsonld_repeated_products_are_deduplicated():
    assert len(parse_html_payload(jsonld([product(), product()]))) == 1


def test_equivalent_metadata_prices_are_not_reported_as_conflicts():
    html = FIXTURE + jsonld(product(sku="DOUBLE-A-A4-70GSM", price="85000.00", currency="vnd"))
    assert len(parse_html_payload(html)) == 1
    html = FIXTURE.replace(b'content="85000">\n  </span>', b'content="85000"><meta itemprop="price" content="85000.00">\n  </span>')
    assert parse_html_payload(html)[0]["price"] == "85000"


def test_empty_script_and_metadata_attributes_do_not_crash_product_extraction():
    html = b'<script type>/* unrelated */</script>' + FIXTURE.replace(
        b'<meta itemprop="sku"', b'<meta itemprop><meta itemprop="sku"',
    )
    assert parse_html_payload(html)[0]["source_sku"] == "DOUBLE-A-A4-70GSM"


def test_conflicting_variants_are_rejected_instead_of_selecting_cheapest():
    with pytest.raises(InvalidRecord, match="ambiguous_html_product"):
        parse_html_payload(jsonld([product(), product(price="99000")]))
    with pytest.raises(InvalidRecord, match="ambiguous_html_product"):
        parse_html_payload(FIXTURE.replace(b'content="85000">\n  </span>', b'content="85000"><meta itemprop="price" content="99000">\n  </span>'))


@pytest.mark.parametrize("document", [
    product(currency=None),
    product(sku=None),
    {"@type": "Product", "sku": "RANGE", "offers": {
        "@type": "AggregateOffer", "lowPrice": "100", "highPrice": "200", "priceCurrency": "VND",
    }},
    {"@type": "Review", "price": "100", "sku": "REVIEW"},
])
def test_incomplete_or_range_prices_are_not_invented(document):
    with pytest.raises(InvalidRecord, match="empty_or_unparseable_html_table"):
        parse_html_payload(jsonld(document))


def test_existing_row_basis_overrides_source_default():
    html = b'<div data-sku="A" data-price="100" data-price-basis="tax_excluded"></div>'
    assert parse_html_payload(html, price_basis="tax_included")[0]["price_basis"] == "tax_excluded"


@pytest.mark.parametrize("value", ["included", True, 1, [], {}])
@pytest.mark.parametrize("method", ["POST", "PATCH"])
def test_api_rejects_invalid_basis_without_modifying_source(value, method):
    with local_pipeline() as p:
        p.api("POST", "/sources", body={"source_id": "paper", "kind": "web"})
        before = p.store.get("tenant-a", "SOURCE#paper")
        body = {"price_basis": value}
        if method == "POST":
            body.update(source_id="paper", kind="web")
        status, _ = p.api(method, "/sources" if method == "POST" else "/sources/paper", body=body)
        assert status == 400
        assert p.store.get("tenant-a", "SOURCE#paper") == before


def test_basis_crud_is_web_only_and_preserves_on_toggle():
    with local_pipeline() as p:
        status, _ = p.api("POST", "/sources", body={
            "source_id": "api", "kind": "api", "price_basis": "tax_included",
        })
        assert status == 400
        status, source = p.api("POST", "/sources", body={
            "source_id": "paper", "kind": "web", "price_basis": "tax_included",
        })
        assert status == 201 and source["price_basis"] == "tax_included"
        status, source = p.api("PATCH", "/sources/paper", body={"enabled": False})
        assert status == 200 and source["price_basis"] == "tax_included"
        assert p.api("GET", "/sources")[1]["data"][0]["price_basis"] == "tax_included"
        status, _ = p.api("PATCH", "/sources/paper", body={"price_basis": None})
        assert status == 200
        assert "price_basis" not in p.api("GET", "/sources")[1]["data"][0]


def test_url_configuration_to_pending_approval_and_matrix_without_aws():
    with local_pipeline() as p:
        url = "https://shop.example.invalid/products/paper"
        p.feeds[url] = FIXTURE
        reference_url = "https://reference.example.invalid/products/paper"
        p.feeds[reference_url] = FIXTURE.replace(b'content="85000"', b'content="90000"')
        assert p.api("POST", "/products", body={"product_id": "paper", "name": "Paper"})[0] == 201
        assert p.api("POST", "/sources", body={
            "source_id": "paper-shop", "kind": "web", "url": url, "price_basis": "tax_included",
        })[0] == 201
        assert p.api("POST", "/sources", body={
            "source_id": "paper-reference", "kind": "web", "url": reference_url, "price_basis": "tax_included",
        })[0] == 201
        assert p.dispatch() == {"sent": 2}
        result = p.run_sources("web")
        assert not result["batchItemFailures"]
        # Changing source settings after capture cannot reinterpret a retry snapshot.
        p.api("PATCH", "/sources/paper-shop", body={"price_basis": "tax_excluded"})
        p.dispatch()
        assert not p.run_sources("web")["batchItemFailures"]
        assert len(p.fetch_calls) == 2
        p.advance(hours=1)
        p.normalize()
        assert not p.matrix()["data"]
        pending = p.store.receipts("tenant-a", status="pending")
        assert len(pending) == 2
        for row in pending:
            assert row["price_basis"] == "tax_included"
            p.store.approve_pending("tenant-a", row["observation_id"], "paper", now=p.now)
        matrix = p.matrix(reference_source="paper-reference")["data"]
        assert len(matrix) == 2
        shop = next(row for row in matrix if row["source_id"] == "paper-shop")
        assert shop["price"] == 85000 and Decimal(str(shop["change_percent"])) == Decimal("-5.56")
        assert p.api("GET", "/matrix", user="viewer-b")[1]["data"] == []
