"""Static HTML extractor adapter for permitted sources."""

import json
import logging
import os
import urllib.error
import urllib.request
from html.parser import HTMLParser

import boto3

from marketdata.contracts import AccessDenied, InvalidRecord, identifier
from marketdata.queue import send_rows
from marketdata.repository import DynamoStore
from services.ingestion.batch import process_source_batch
from services.ingestion.fetch_errors import RetryableFetchError, http_error_to_exception

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

REQUIRED = {"source_sku", "price", "currency", "observed_at"}
MAX_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 10

HEADER_MAPPING = {
    "sku": "source_sku",
    "source_sku": "source_sku",
    "product_sku": "source_sku",
    "item_sku": "source_sku",
    "mã_sku": "source_sku",
    "product_code": "source_sku",
    "price": "price",
    "giá": "price",
    "don_gia": "price",
    "unit_price": "price",
    "currency": "currency",
    "tiền_tệ": "currency",
    "tien_te": "currency",
    "đơn_vị": "currency",
    "observed_at": "observed_at",
    "thời_điểm": "observed_at",
    "thoi_diem": "observed_at",
    "date": "observed_at",
    "timestamp": "observed_at",
    "price_basis": "price_basis",
}


class HTMLTablePriceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._headers = []
        self._current_row_cells = []
        self._current_cell_text = []

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        # Check if element itself has data attributes for price observation
        if "data-sku" in attr_dict and "data-price" in attr_dict:
            row = {
                "source_sku": attr_dict["data-sku"].strip(),
                "price": attr_dict["data-price"].strip(),
                "currency": attr_dict.get("data-currency", "VND").strip().upper(),
                "observed_at": attr_dict.get("data-observed-at", "").strip(),
            }
            if "data-price-basis" in attr_dict:
                row["price_basis"] = attr_dict["data-price-basis"].strip()
            self.rows.append(row)
            return

        if tag == "table":
            self._in_table = True
        elif tag == "tr" and self._in_table:
            self._in_row = True
            self._current_row_cells = []
        elif tag in ("th", "td") and self._in_row:
            self._in_cell = True
            self._current_cell_text = []

    def handle_data(self, data):
        if self._in_cell:
            self._current_cell_text.append(data)

    def handle_endtag(self, tag):
        if tag in ("th", "td") and self._in_cell:
            cell_content = "".join(self._current_cell_text).strip()
            self._current_row_cells.append(cell_content)
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            self._in_row = False
            normalized_headers = [
                c.casefold().strip().replace("-", "_").replace(" ", "_")
                for c in self._current_row_cells
            ]
            mapped_headers = [HEADER_MAPPING.get(c, c) for c in normalized_headers]
            if not self._headers and REQUIRED <= set(mapped_headers):
                # Header row
                self._headers = mapped_headers
            elif self._headers and len(self._current_row_cells) == len(self._headers):
                row = dict(zip(self._headers, self._current_row_cells, strict=False))
                self.rows.append(row)
        elif tag == "table":
            self._in_table = False


def default_fetcher(url, headers=None, timeout=TIMEOUT_SECONDS):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "MarketDataPipeline/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise InvalidRecord("html_response_exceeds_1_mib")
            return data
    except urllib.error.HTTPError as exc:
        raise http_error_to_exception(exc, "web") from exc
    except urllib.error.URLError as exc:
        raise RetryableFetchError(f"web_connection_error_{exc.reason}") from exc
    except (TimeoutError, ConnectionError, OSError) as exc:
        raise RetryableFetchError(f"web_connection_error_{exc}") from exc


def parse_html_payload(data):
    if len(data) > MAX_BYTES:
        raise InvalidRecord("html_response_exceeds_1_mib")
    try:
        html_str = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        try:
            html_str = data.decode("latin-1")
        except Exception:
            raise InvalidRecord("invalid_html_encoding") from exc

    parser = HTMLTablePriceParser()
    try:
        parser.feed(html_str)
    except Exception as exc:
        raise InvalidRecord("invalid_html_syntax") from exc

    if not parser.rows:
        raise InvalidRecord("empty_or_unparseable_html_table")
    if len(parser.rows) > 1000:
        raise InvalidRecord("html_exceeds_1000_rows")

    validated_rows = []
    for row in parser.rows:
        if "tenant_id" in row:
            raise InvalidRecord("tenant_not_allowed_in_record")
        if not REQUIRED <= set(row.keys()):
            raise InvalidRecord("missing_required_html_fields")
        cleaned = {
            "source_sku": str(row["source_sku"]).strip(),
            "price": str(row["price"]).strip(),
            "currency": str(row["currency"]).strip().upper(),
            "observed_at": str(row["observed_at"]).strip(),
        }
        if any(not value for value in cleaned.values()):
            raise InvalidRecord("empty_required_html_field")
        if "price_basis" in row:
            cleaned["price_basis"] = str(row["price_basis"]).strip()
        validated_rows.append(cleaned)

    return validated_rows


def process_event(event, store, sqs, transform_queue, fetcher=None):
    fetch = fetcher or default_fetcher

    if event.get("schema_version") != 1:
        raise InvalidRecord("unsupported_schema_version")
    if event.get("kind") != "web":
        raise AccessDenied("web_job_required")

    tenant_id = identifier(event["tenant_id"])
    source_id = identifier(event["source_id"])
    job_id = identifier(event["job_id"])

    job = store.get(tenant_id, "JOB#" + job_id)
    if not job:
        raise AccessDenied("job_not_found")
    if job.get("source_id") != source_id or job.get("kind") != "web":
        raise AccessDenied("web_job_required")

    source = store.get(tenant_id, "SOURCE#" + source_id)
    if not source or source.get("kind") != "web" or not source.get("enabled"):
        store.tenant(tenant_id)
        store.update_job(
            tenant_id, job_id, status="failed", error="source_disabled_or_missing", row_count=0
        )
        return
    store.require_job({"tenant_id": tenant_id, "source_id": source_id, "job_id": job_id})

    url = source.get("url") or source.get("endpoint")
    if not url:
        store.update_job(tenant_id, job_id, status="failed", error="missing_web_url", row_count=0)
        return

    try:
        raw_data = fetch(url, headers=source.get("headers"))
        rows = parse_html_payload(raw_data)
    except RetryableFetchError:
        raise
    except (InvalidRecord, AccessDenied) as exc:
        store.update_job(tenant_id, job_id, status="failed", error=str(exc), row_count=0)
        return
    except Exception as exc:
        store.update_job(
            tenant_id, job_id, status="failed", error=f"fetch_failed_{exc}", row_count=0
        )
        return

    values = [
        {
            "schema_version": 1,
            "tenant_id": tenant_id,
            "source_id": source_id,
            "job_id": job_id,
            "row_number": i,
            "record": row,
        }
        for i, row in enumerate(rows, 1)
    ]

    # Validate size limits (32 KiB per row)
    if any(len(json.dumps(v, ensure_ascii=False).encode()) > 32 * 1024 for v in values):
        store.update_job(
            tenant_id, job_id, status="failed", error="row_exceeds_32_kib", row_count=0
        )
        return

    store.update_job(tenant_id, job_id, row_count=len(rows), status="normalizing")
    send_rows(sqs, transform_queue, values)


def process_batch(event, store, sqs, queue, fetcher=None, input_queue_url=None):
    return process_source_batch(
        event,
        store,
        sqs,
        queue,
        input_queue_url,
        "web",
        process_event,
        fetcher=fetcher,
    )


def handler(event, context):
    return process_batch(
        event,
        DynamoStore(),
        boto3.client("sqs"),
        os.environ["TRANSFORM_QUEUE_URL"],
        input_queue_url=os.environ["WEB_QUEUE_URL"],
    )
