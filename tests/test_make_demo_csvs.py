from marketdata.contracts import iso
from marketdata.normalize import normalize
from scripts.make_demo_csvs import write_demo_csvs, write_vendor_c_feed
from services.ingestion.api_worker import parse_api_payload
from services.ingestion.csv_worker import parse_csv
from tests.conftest import NOW


def test_vendor_c_feed_is_timestamped_now_and_passes_the_api_worker(tmp_path):
    path = write_vendor_c_feed(tmp_path / "vendor-c-api.json", NOW)

    rows = parse_api_payload(path.read_bytes())
    assert [row["source_sku"] for row in rows] == ["IP15-128", "IP15-256", "S24-256", "MBA-M3", "AIRPODS-PRO2"]
    assert {row["observed_at"] for row in rows} == {iso(NOW)}
    for row in rows:
        assert normalize(row, {}, NOW)["currency"] == "VND"


def test_demo_files_pass_the_csv_worker_and_normalizer(tmp_path):
    paths = write_demo_csvs(tmp_path, NOW)

    assert [path.name for path in paths] == ["demo-vendor-a.csv", "demo-vendor-b.csv"]
    for path in paths:
        rows = parse_csv(path.read_bytes())
        assert len(rows) == 5
        for row in rows:
            result = normalize(row, {}, NOW)
            assert result["price_basis"] == "tax_included"
            assert result["currency"] == "VND"
