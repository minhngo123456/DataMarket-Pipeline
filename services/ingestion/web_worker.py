"""Static HTML extractor adapter for permitted sources."""

import logging
import os
import re
import unicodedata
import urllib.error
import urllib.request
from datetime import UTC, datetime
from html.parser import HTMLParser

import boto3

from marketdata.contracts import AccessDenied, InvalidRecord, identifier, iso, timestamp
from marketdata.queue import send_rows
from marketdata.repository import DynamoStore
from services.ingestion.batch import process_source_batch
from services.ingestion.collection import collection_error_code
from services.ingestion.fetch_errors import RetryableFetchError, http_error_to_exception
from services.ingestion.safe_http import open_public
from services.ingestion.snapshot import SourceSnapshots

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

MAX_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 10

HEADER_MAPPING = {
    "sku": "source_sku",
    "source_sku": "source_sku",
    "product_sku": "source_sku",
    "item_sku": "source_sku",
    "mã_sku": "source_sku",
    "mã_sản_phẩm": "source_sku",
    "ma_sku": "source_sku",
    "ma_san_pham": "source_sku",
    "product_code": "source_sku",
    "ma_hang": "source_sku",
    "mã_hàng": "source_sku",
    "price": "price",
    "giá": "price",
    "gia": "price",
    "giá_bán": "price",
    "gia_ban": "price",
    "don_gia": "price",
    "unit_price": "price",
    "current_price": "price",
    "final_price": "price",
    "sale_price": "sale_price",
    "discount_price": "sale_price",
    "promotional_price": "sale_price",
    "promotion_price": "sale_price",
    "giá_khuyến_mãi": "sale_price",
    "gia_khuyen_mai": "sale_price",
    "giá_ưu_đãi": "sale_price",
    "gia_uu_dai": "sale_price",
    "giá_sau_giảm": "sale_price",
    "gia_sau_giam": "sale_price",
    "regular_price": "regular_price",
    "original_price": "regular_price",
    "list_price": "regular_price",
    "old_price": "regular_price",
    "giá_gốc": "regular_price",
    "gia_goc": "regular_price",
    "giá_niêm_yết": "regular_price",
    "gia_niem_yet": "regular_price",
    "promotion_active": "promotion_active",
    "promo_active": "promotion_active",
    "discount_active": "promotion_active",
    "khuyến_mãi_đang_áp_dụng": "promotion_active",
    "khuyen_mai_dang_ap_dung": "promotion_active",
    "currency": "currency",
    "tiền_tệ": "currency",
    "tien_te": "currency",
    "đơn_vị": "currency",
    "don_vi": "currency",
    "observed_at": "observed_at",
    "thời_điểm": "observed_at",
    "thoi_diem": "observed_at",
    "date": "observed_at",
    "timestamp": "observed_at",
    "price_basis": "price_basis",
}

_INACTIVE_PROMOTION_VALUES = {
    "0",
    "false",
    "no",
    "n",
    "inactive",
    "expired",
    "không",
    "khong",
    "không áp dụng",
    "khong ap dung",
    "hết hạn",
    "het han",
    "not active",
    "no promotion",
}
_EMPTY_PRICE_VALUES = {"", "-", "—", "–", "n/a", "na", "không có", "khong co"}


def _normalize_header(value):
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    normalized = re.sub(r"\([^)]*\)|\[[^]]*\]", "", normalized)
    normalized = re.sub(r"[*:]+$", "", normalized).strip()
    return re.sub(r"[\s-]+", "_", normalized)


def _grouped_integer_is_valid(value, separator):
    groups = value.split(separator)
    return (
        len(groups) > 1
        and 1 <= len(groups[0]) <= 3
        and groups[0].isdigit()
        and all(len(group) == 3 and group.isdigit() for group in groups[1:])
    )


def _normalize_price(value):
    """Convert common displayed price formats to the Normalizer's decimal contract."""
    price_text = unicodedata.normalize("NFKC", str(value or "")).strip()
    price_text = re.sub(r"(?i)(?:vnd|vnđ|usd|eur|gbp|đồng|₫)", "", price_text)
    price_text = re.sub(r"(?i)^đ\s*|\s*đ$", "", price_text)
    price_text = price_text.translate(str.maketrans("", "", "$€£¥"))
    price_text = re.sub(r"[\s\u00a0\u202f]", "", price_text)
    if not price_text or not re.fullmatch(r"[0-9.,]+", price_text):
        raise InvalidRecord("invalid_html_price")

    has_dot = "." in price_text
    has_comma = "," in price_text
    if has_dot and has_comma:
        decimal_separator = "." if price_text.rfind(".") > price_text.rfind(",") else ","
        thousands_separator = "," if decimal_separator == "." else "."
        if price_text.count(decimal_separator) != 1:
            raise InvalidRecord("invalid_html_price")
        integer_part, decimal_part = price_text.rsplit(decimal_separator, 1)
        if (
            not decimal_part.isdigit()
            or not 1 <= len(decimal_part) <= 2
            or not _grouped_integer_is_valid(integer_part, thousands_separator)
        ):
            raise InvalidRecord("invalid_html_price")
        price_text = integer_part.replace(thousands_separator, "") + "." + decimal_part
    elif has_dot or has_comma:
        separator = "." if has_dot else ","
        parts = price_text.split(separator)
        if len(parts) > 2:
            if _grouped_integer_is_valid(price_text, separator):
                price_text = price_text.replace(separator, "")
            else:
                integer_part, decimal_part = price_text.rsplit(separator, 1)
                if (
                    not decimal_part.isdigit()
                    or not 1 <= len(decimal_part) <= 2
                    or not _grouped_integer_is_valid(integer_part, separator)
                ):
                    raise InvalidRecord("invalid_html_price")
                price_text = integer_part.replace(separator, "") + "." + decimal_part
        else:
            integer_part, tail = parts
            if not integer_part.isdigit() or not tail.isdigit():
                raise InvalidRecord("invalid_html_price")
            if len(tail) == 3 and 1 <= len(integer_part) <= 3:
                price_text = integer_part + tail
            elif 1 <= len(tail) <= 2:
                price_text = integer_part + "." + tail
            else:
                raise InvalidRecord("invalid_html_price")

    if not re.fullmatch(r"\d{1,15}(?:\.\d{1,2})?", price_text):
        raise InvalidRecord("invalid_html_price")
    return price_text


def _present_value(value):
    if value is None:
        return None
    text = str(value).strip()
    if text.casefold() in _EMPTY_PRICE_VALUES:
        return None
    return text


def _effective_price(row):
    sale_price = _present_value(row.get("sale_price"))
    promotion_active = str(row.get("promotion_active", "")).strip().casefold()
    if sale_price and promotion_active not in _INACTIVE_PROMOTION_VALUES:
        return sale_price
    return _present_value(row.get("price")) or _present_value(row.get("regular_price"))


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
        price_attributes = {
            "data-price",
            "data-current-price",
            "data-sale-price",
            "data-promo-price",
            "data-original-price",
            "data-list-price",
        }
        if "data-sku" in attr_dict and price_attributes.intersection(attr_dict):
            row = {
                "source_sku": attr_dict.get("data-sku", ""),
                "price": attr_dict.get("data-price") or attr_dict.get("data-current-price"),
                "sale_price": attr_dict.get("data-sale-price")
                or attr_dict.get("data-promo-price"),
                "regular_price": attr_dict.get("data-original-price")
                or attr_dict.get("data-list-price"),
                "promotion_active": attr_dict.get("data-promotion-active")
                or attr_dict.get("data-sale-active"),
                "currency": attr_dict.get("data-currency") or "VND",
                "observed_at": attr_dict.get("data-observed-at"),
            }
            if "data-price-basis" in attr_dict:
                row["price_basis"] = attr_dict["data-price-basis"]
            self.rows.append(row)
            return

        if tag == "table":
            self._in_table = True
            self._headers = []
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
            mapped_headers = [
                HEADER_MAPPING.get(_normalize_header(cell), _normalize_header(cell))
                for cell in self._current_row_cells
            ]
            has_product_key = "source_sku" in mapped_headers
            has_price_column = any(
                field in mapped_headers for field in ("price", "sale_price", "regular_price")
            )
            if not self._headers and has_product_key and has_price_column:
                # Header row
                self._headers = mapped_headers
            elif self._headers and len(self._current_row_cells) == len(self._headers):
                row = dict(zip(self._headers, self._current_row_cells, strict=False))
                self.rows.append(row)
        elif tag == "table":
            self._in_table = False
            self._headers = []


def default_fetcher(url, headers=None, timeout=TIMEOUT_SECONDS):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "MarketDataPipeline/1.0"})
    try:
        with open_public(req, timeout=timeout) as response:
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


def parse_html_payload(data, observed_at=None):
    if len(data) > MAX_BYTES:
        raise InvalidRecord("html_response_exceeds_1_mib")
    fallback_observed_at = observed_at if observed_at is not None else iso(datetime.now(UTC))
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
        source_sku = _present_value(row.get("source_sku"))
        price_value = _effective_price(row)
        if not source_sku or not price_value:
            raise InvalidRecord("missing_required_html_fields")
        observed_value = _present_value(row.get("observed_at")) or fallback_observed_at
        try:
            normalized_observed_at = iso(timestamp(observed_value))
        except InvalidRecord as exc:
            raise InvalidRecord("invalid_html_observed_at") from exc
        cleaned = {
            "source_sku": source_sku,
            "price": _normalize_price(price_value),
            "currency": str(_present_value(row.get("currency")) or "VND").upper(),
            "observed_at": normalized_observed_at,
        }
        if "price_basis" in row:
            price_basis = _present_value(row["price_basis"])
            if price_basis:
                cleaned["price_basis"] = price_basis
        validated_rows.append(cleaned)

    return validated_rows


def process_event(event, store, sqs, transform_queue, fetcher=None, snapshots=None):
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
    store.tenant(tenant_id)
    source = store.get(tenant_id, "SOURCE#" + source_id)
    if source and source.get("kind") == "web" and not store.source_job_matches(source, job):
        store.fail_source_job(tenant_id, job_id, source_id, "web", "source_generation_changed")
        return
    if job.get("status") == "completed":
        store.record_source_collection(tenant_id, source_id, job_id, "web", "succeeded")
        return

    if not source or source.get("kind") != "web" or not source.get("enabled"):
        store.tenant(tenant_id)
        store.fail_source_job(tenant_id, job_id, source_id, "web", "source_disabled_or_missing")
        return
    if job.get("status") == "failed":
        store.record_source_collection(
            tenant_id, source_id, job_id, "web", "failed",
            collection_error_code(job.get("error"), "web"),
        )
        return
    store.require_job({"tenant_id": tenant_id, "source_id": source_id, "job_id": job_id})
    store.record_source_collection(tenant_id, source_id, job_id, "web", "running")

    def load_rows():
        # Resolve the job's source generation again before the first capture;
        # an old delivery must never fetch a replacement source's configuration.
        _, current_source, _ = store.require_job(
            {"tenant_id": tenant_id, "source_id": source_id, "job_id": job_id}
        )
        url = current_source.get("url") or current_source.get("endpoint")
        if not url:
            raise InvalidRecord("missing_web_url")
        raw_data = fetch(url, headers=current_source.get("headers"))
        # Source timestamps take precedence. Missing times use the job schedule
        # (or UTC now) once, then remain immutable in the persisted snapshot.
        return parse_html_payload(
            raw_data, observed_at=job.get("scheduled_at") or event.get("scheduled_at")
        )

    try:
        snapshot = (snapshots or SourceSnapshots()).get_or_create(event, job, load_rows)
    except RetryableFetchError:
        raise
    except (InvalidRecord, AccessDenied) as exc:
        code = collection_error_code(exc, "web")
        if store.fail_source_job(tenant_id, job_id, source_id, "web", code, require_unbound=True):
            store.record_source_collection(tenant_id, source_id, job_id, "web", "failed", code)
        return

    committed_job = store.bind_source_snapshot(
        tenant_id, job_id, source_id, "web", snapshot.descriptor, len(snapshot.values)
    )
    if committed_job["status"] == "completed":
        store.record_source_collection(tenant_id, source_id, job_id, "web", "succeeded")
        return
    current_source = store.get(tenant_id, "SOURCE#" + source_id)
    if current_source and not store.source_job_matches(current_source, committed_job):
        store.fail_source_job(tenant_id, job_id, source_id, "web", "source_generation_changed")
        return
    store.require_job({"tenant_id": tenant_id, "source_id": source_id, "job_id": job_id})
    send_rows(sqs, transform_queue, snapshot.values)
    store.record_source_collection(tenant_id, source_id, job_id, "web", "succeeded")


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
