"""Write demo price CSVs for vendor-a and vendor-b, timestamped now.

The price matrix only covers the last 24 hours, so regenerate these on each demo day and
upload them on the Upload page. Prices are made-up demo values, not market data.
--vendor-c-feed also rewrites the static REST feed that vendor-c polls; deploy the web
app afterwards so the next scheduled run picks it up.

Usage:
    python -m scripts.make_demo_csvs --out-dir <folder> [--vendor-c-feed apps/web/public/demo/vendor-c-api.json]
"""

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from marketdata.contracts import iso

FIELDS = ("source_sku", "price", "currency", "observed_at", "price_basis")
DEMO_PRICES = {
    "vendor-a": {
        "IP15-128": 19490000,
        "IP15-256": 22490000,
        "S24-256": 18290000,
        "MBA-M3": 26590000,
        "AIRPODS-PRO2": 5290000,
    },
    "vendor-b": {
        "IP15-128": 19790000,
        "IP15-256": 22990000,
        "S24-256": 17990000,
        "MBA-M3": 27190000,
        "AIRPODS-PRO2": 5490000,
    },
}
VENDOR_C_PRICES = {
    "IP15-128": 19590000,
    "IP15-256": 22790000,
    "S24-256": 18190000,
    "MBA-M3": 26790000,
    "AIRPODS-PRO2": 5390000,
}


def write_vendor_c_feed(path, now):
    rows = [
        {"source_sku": sku, "price": price, "currency": "VND", "observed_at": iso(now), "price_basis": "tax_included"}
        for sku, price in VENDOR_C_PRICES.items()
    ]
    lines = ",\n".join(f"    {json.dumps(row)}" for row in rows)
    Path(path).write_text('{\n  "data": [\n' + lines + "\n  ]\n}\n", encoding="utf-8", newline="\n")
    return Path(path)


def write_demo_csvs(out_dir, now):
    paths = []
    for source_id, prices in DEMO_PRICES.items():
        path = Path(out_dir) / f"demo-{source_id}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            for source_sku, price in prices.items():
                writer.writerow(
                    {
                        "source_sku": source_sku,
                        "price": price,
                        "currency": "VND",
                        "observed_at": iso(now),
                        "price_basis": "tax_included",
                    }
                )
        paths.append(path)
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--vendor-c-feed", help="Path of the vendor-c JSON feed to rewrite")
    args = parser.parse_args()
    now = datetime.now(UTC)
    for path in write_demo_csvs(args.out_dir, now):
        print(path)
    if args.vendor_c_feed:
        print(write_vendor_c_feed(args.vendor_c_feed, now))


if __name__ == "__main__":
    main()
