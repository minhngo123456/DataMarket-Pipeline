from marketdata.normalize import normalize
from scripts.make_demo_csvs import write_demo_csvs
from services.ingestion.csv_worker import parse_csv
from tests.conftest import NOW


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
