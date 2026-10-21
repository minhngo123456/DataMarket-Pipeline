"""API HTTP Response helper and JSON serialization utilities.

Ensures Decimal types from DynamoDB are properly serialized (as strings or floats)
and provides standardized HTTP JSON responses with CORS headers.
"""

import json
from decimal import Decimal


def decimal_serializer(obj):
    """JSON serializer for objects not serializable by default json code."""
    if isinstance(obj, Decimal):
        # Convert Decimal to string if needed, or int/float if exact
        if obj % 1 == 0:
            return int(obj)
        return str(obj)
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


def build_response(status_code: int, body: dict | list | None = None, headers: dict | None = None) -> dict:
    """Build a standard AWS API Gateway HTTP API response payload."""
    default_headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization,X-Amz-Date,X-Api-Key",
        "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
    }
    if headers:
        default_headers.update(headers)

    payload = ""
    if body is not None:
        payload = json.dumps(body, default=decimal_serializer)

    return {
        "statusCode": status_code,
        "headers": default_headers,
        "body": payload,
    }


def ok(data: dict | list | None = None, status_code: int = 200) -> dict:
    """Convenience helper for HTTP 200/201 Success."""
    return build_response(status_code, body=data)


def paginated(data: list, total: int, page: int = 1, limit: int = 20, extra: dict | None = None) -> dict:
    """Convenience helper for paginated responses."""
    body = {
        "data": data,
        "total": total,
        "page": page,
        "limit": limit,
    }
    if extra:
        body.update(extra)
    return ok(body)


def error(code: str, message: str, status_code: int = 400, details: dict | None = None) -> dict:
    """Convenience helper for standard error responses."""
    err_body = {
        "error": {
            "code": code,
            "message": message,
        }
    }
    if details:
        err_body["error"]["details"] = details
    return build_response(status_code, body=err_body)
