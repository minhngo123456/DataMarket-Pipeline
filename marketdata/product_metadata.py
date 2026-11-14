"""Optional display metadata; never used as a product identity or price input."""

import re
from urllib.parse import urlsplit, urlunsplit


def product_metadata(raw):
    result = {}
    if raw.get("extraction_method") == "ai_rules":
        result["extraction_method"] = "ai_rules"
    for key, limit in (("source_name", 500), ("source_variant", 200)):
        value = raw.get(key)
        if isinstance(value, str):
            value = re.sub(r"\s+", " ", value).strip()[:limit]
            if value:
                result[key] = value
    value = raw.get("source_url")
    if isinstance(value, str) and len(value) <= 2048 and not re.search(r"[\x00-\x20\x7f]", value):
        try:
            parts = urlsplit(value)
            if parts.scheme in ("https", "http") and parts.hostname and not parts.username and not parts.password:
                result["source_url"] = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        except ValueError:
            pass
    return result
