"""Write demo price files for vendor-a, vendor-b (CSV) and vendor-c (API feed), timestamped now.

Prices come from scripts/demo_prices.json: 20 phones with the listed prices of Thế Giới Di Động
(vendor-a), CellphoneS (vendor-b) and FPT Shop (vendor-c) at the time in `captured_at`. A null
price means that shop does not sell that exact variant, so the row is left out.

The price matrix only covers the last 24 hours, so regenerate these on each demo day.

Usage:
    python -m scripts.make_demo_csvs --out-dir <folder> [--vendor-c-feed <path>]
"""

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from marketdata.contracts import iso

FIELDS = ("source_sku", "price", "currency", "observed_at", "price_basis")
PRICES_FILE = Path(__file__).with_name("demo_prices.json")
CSV_SOURCES = ("vendor-a", "vendor-b")


def load_prices(path=PRICES_FILE):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rows_for(source_id, now, data=None):
    data = data or load_prices()
    return [
        {
            "source_sku": product["sku"],
            "price": product["prices"][source_id],
            "currency": "VND",
            "observed_at": iso(now),
            "price_basis": "tax_included",
        }
        for product in data["products"]
        if product["prices"].get(source_id) is not None
    ]


def write_demo_csvs(out_dir, now, data=None):
    data = data or load_prices()
    paths = []
    for source_id in CSV_SOURCES:
        path = Path(out_dir) / f"demo-{source_id}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows_for(source_id, now, data))
        paths.append(path)
    return paths


def write_vendor_c_feed(path, now, data=None):
    rows = rows_for("vendor-c", now, data)
    lines = ",\n".join("    " + json.dumps(row, ensure_ascii=False) for row in rows)
    Path(path).write_text('{\n  "data": [\n' + lines + "\n  ]\n}\n", encoding="utf-8")
    return Path(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--vendor-c-feed", help="also rewrite this vendor-c JSON feed")
    args = parser.parse_args()
    now = datetime.now(UTC)
    for path in write_demo_csvs(args.out_dir, now):
        print(path)
    if args.vendor_c_feed:
        print(write_vendor_c_feed(args.vendor_c_feed, now))


if __name__ == "__main__":
    main()
