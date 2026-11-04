"""Read JSON secrets without storing credentials in tenant records or logs."""

import json
import time

import boto3
from botocore.config import Config

_client = None
_cache = {}


def secret_field(arn, field, client=None):
    """Cache secret fields for five minutes in a warm Lambda process."""
    global _client
    key = (arn, field)
    now = time.monotonic()
    if client is None and key in _cache and _cache[key][0] > now:
        return _cache[key][1]
    if client is None:
        if _client is None:
            _client = boto3.client(
                "secretsmanager",
                config=Config(
                    connect_timeout=3,
                    read_timeout=5,
                    retries={"mode": "standard", "total_max_attempts": 2},
                ),
            )
        client = _client
        cache_result = True
    else:
        cache_result = False
    response = client.get_secret_value(SecretId=arn)
    try:
        value = json.loads(response.get("SecretString", ""))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("secret_must_be_json") from exc
    if not isinstance(value, dict) or not isinstance(value.get(field), str) or not value[field].strip():
        raise ValueError("secret_field_missing")
    result = value[field].strip()
    if cache_result:
        if len(_cache) >= 32:
            _cache.clear()
        _cache[key] = (now + 300, result)
    return result
