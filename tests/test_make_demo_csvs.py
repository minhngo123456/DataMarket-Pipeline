import json

from marketdata.normalize import normalize
from scripts.make_demo_csvs import load_prices, write_demo_csvs, write_vendor_c_feed
from services.ingestion.api_worker import parse_api_payload
from services.ingestion.csv_worker import parse_csv
from tests.conftest import NOW


def expected_rows(source_id):
    return sum(1 for p in load_prices()["products"] if p["prices"].get(source_id) is not None)


def test_price_file_has_20_products_with_unique_skus():
    products = load_prices()["products"]
    assert len(products) == 20
    assert len({p["sku"] for p in products}) == 20


def test_demo_files_pass_the_csv_worker_and_normalizer(tmp_path):
    paths = write_demo_csvs(tmp_path, NOW)

    assert [path.name for path in paths] == ["demo-vendor-a.csv", "demo-vendor-b.csv"]
    for source_id, path in zip(("vendor-a", "vendor-b"), paths, strict=True):
        rows = parse_csv(path.read_bytes())
        assert len(rows) == expected_rows(source_id)
        for row in rows:
            result = normalize(row, {}, NOW)
            assert result["price_basis"] == "tax_included"
            assert result["currency"] == "VND"


def test_vendor_c_feed_passes_the_api_worker(tmp_path):
    path = write_vendor_c_feed(tmp_path / "feed.json", NOW)

    json.loads(path.read_text(encoding="utf-8"))
    rows = parse_api_payload(path.read_bytes())
    assert len(rows) == expected_rows("vendor-c")
    for row in rows:
        assert normalize(row, {}, NOW)["currency"] == "VND"
