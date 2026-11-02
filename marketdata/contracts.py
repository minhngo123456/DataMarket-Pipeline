"""Small, explicit contracts shared with Hướng's workers and Hiếu's API."""

import hashlib
import re
from datetime import UTC, datetime

# Mirrored by apps/web/src/pages/Upload/csvPreview.js; tests/test_api.py keeps them equal.
MAX_CSV_BYTES = 1024 * 1024
MAX_CSV_ROWS = 1000


class InvalidRecord(ValueError):
    pass


class AccessDenied(ValueError):
    pass


class QuotaExceeded(ValueError):
    pass


class Conflict(ValueError):
    pass


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise InvalidRecord("invalid_identifier")
    return value


def timestamp(value):
    if not isinstance(value, str):
        raise InvalidRecord("invalid_timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidRecord("invalid_timestamp") from exc
    if result.tzinfo is None:
        raise InvalidRecord("timezone_required")
    return result.astimezone(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def stable_id(*parts):
    return hashlib.sha256("\x00".join(str(x) for x in parts).encode()).hexdigest()[:32]


def observation_content_id(tenant_id, source_id, source_sku, observed_at, price_basis, price):
    """Identify a price point across jobs. Fixed-point price so DynamoDB's number form cannot change the id."""
    return stable_id(tenant_id, source_id, source_sku, observed_at, price_basis, format(price, "f"))


def envelope(value):
    if (
        not isinstance(value, dict)
        or type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
    ):
        raise InvalidRecord("unsupported_schema_version")
    for key in ("tenant_id", "job_id", "source_id"):
        identifier(value.get(key))
    row = value.get("row_number")
    if type(row) is not int or not 1 <= row <= 1000:
        raise InvalidRecord("invalid_row_number")
    if not isinstance(value.get("record"), dict):
        raise InvalidRecord("invalid_record")
    return value
