"""Validate values without guessing separators, currencies or product variants."""

import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from marketdata.contracts import InvalidRecord, iso, timestamp


def sku(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 150:
        raise InvalidRecord("invalid_sku")
    return unicodedata.normalize("NFKC", value).strip().upper()


def amount(value):
    # A decimal point is allowed. Thousands separators must be removed by the source adapter.
    if isinstance(value, bool) or not re.fullmatch(r"\d{1,15}(?:\.\d{1,2})?", str(value)):
        raise InvalidRecord("invalid_price")
    result = Decimal(str(value))
    if result <= 0:
        raise InvalidRecord("price_must_be_positive")
    return result


def normalize(raw, source, now):
    if "tenant_id" in raw:
        raise InvalidRecord("tenant_not_allowed_in_row")
    observed = timestamp(raw.get("observed_at"))
    if observed < now - timedelta(days=30):
        raise InvalidRecord("outside_retention_window")
    if observed > now + timedelta(minutes=5):
        raise InvalidRecord("future_timestamp")
    price = amount(raw.get("price"))
    currency = raw.get("currency")
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise InvalidRecord("invalid_currency")
    converted = price
    fx = None
    if currency != "VND":
        fx = source.get("fx")
        try:
            if not isinstance(fx, dict) or fx["currency"] != currency:
                raise ValueError
            effective = date.fromisoformat(fx["effective_date"])
            rate = Decimal(str(fx["rate"]))
            if not rate.is_finite() or rate <= 0 or effective > observed.date():
                raise ValueError
            converted = price * rate
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise InvalidRecord("missing_or_invalid_fx") from exc
    basis = raw.get("price_basis", "unknown")
    if basis not in ("tax_included", "tax_excluded", "unknown"):
        raise InvalidRecord("invalid_price_basis")
    result = {
        "source_sku": sku(raw.get("source_sku")),
        "original_price": price,
        "original_currency": currency,
        "price": converted.quantize(Decimal("1")),
        "currency": "VND",
        "price_basis": basis,
        "observed_at": iso(observed),
        "expires_at": int((observed + timedelta(days=30)).timestamp()),
    }
    if result["price"] <= 0:
        raise InvalidRecord("converted_price_must_be_positive")
    if fx:
        result["fx"] = {"rate": str(fx["rate"]), "effective_date": fx["effective_date"]}
    return result
