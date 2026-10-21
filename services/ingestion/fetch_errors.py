"""Shared HTTP error classification and retry timing for source extractors."""

import math
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from marketdata.contracts import AccessDenied, InvalidRecord

MAX_RETRY_DELAY_SECONDS = 300
BASE_RETRY_DELAY_SECONDS = 30
MAX_RECEIVE_COUNT = 5


class RetryableFetchError(RuntimeError):
    """A temporary upstream failure that should be retried by the source queue."""

    def __init__(self, reason, retry_after_seconds=None):
        super().__init__(reason)
        self.retry_after_seconds = retry_after_seconds


def parse_retry_after(value, now=None):
    """Parse delta-seconds or an HTTP date, bounded by the queue visibility limit."""
    if not value:
        return None
    try:
        delay = int(value.strip())
    except (AttributeError, TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        delay = math.ceil((retry_at - (now or datetime.now(UTC))).total_seconds())
    return max(0, min(delay, MAX_RETRY_DELAY_SECONDS))


def http_error_to_exception(error, source_name):
    """Map an HTTP response to a retryable or terminal worker exception."""
    status = error.code
    if status == 429 or status == 408 or 500 <= status <= 599:
        headers = error.headers
        retry_after = parse_retry_after(headers.get("Retry-After") if headers else None)
        return RetryableFetchError(
            f"{source_name}_http_retryable_{status}", retry_after_seconds=retry_after
        )
    if status in (401, 403):
        return AccessDenied(f"{source_name}_access_denied_{status}")
    return InvalidRecord(f"{source_name}_http_error_{status}")


def retry_delay_seconds(error, receive_count):
    """Use bounded exponential backoff, honoring longer Retry-After values."""
    try:
        attempt = max(1, int(receive_count))
    except (TypeError, ValueError):
        attempt = 1
    backoff = min(BASE_RETRY_DELAY_SECONDS * (2 ** (attempt - 1)), MAX_RETRY_DELAY_SECONDS)
    requested = error.retry_after_seconds or 0
    return min(max(backoff, requested), MAX_RETRY_DELAY_SECONDS)
