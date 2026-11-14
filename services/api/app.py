"""API Gateway HTTP API Lambda Router.

Handles incoming API Gateway v2 HTTP events, authenticates requests against Cognito JWT
and DynamoDB membership, routes to respective handlers, and enforces RBAC and quota limits.
Implements the full 18-endpoint contract defined in docs/api-contract-frontend.md.
"""

import logging
import os
import re

from marketdata.contracts import AccessDenied, Conflict, InvalidRecord, QuotaExceeded
from marketdata.repository import DynamoStore
from services.api.auth import authenticate_and_authorize
from services.api.response import error
from services.api.routes.auth_me import handle_get_me
from services.api.routes.catalog import handle_get_products, handle_post_catalog
from services.api.routes.dashboard import handle_get_alerts, handle_get_dashboard_stats
from services.api.routes.history import handle_get_history
from services.api.routes.matrix import handle_get_matrix
from services.api.routes.members import (
    handle_get_members,
    handle_invite_member,
    handle_patch_member,
)
from services.api.routes.pending import handle_approve_pending_sku, handle_get_pending_skus
from services.api.routes.reports import (
    handle_get_report_detail,
    handle_get_reports,
    handle_post_trigger_report,
)
from services.api.routes.sources import (
    handle_delete_source,
    handle_get_sources,
    handle_patch_source,
    handle_post_sources,
)
from services.api.routes.tenants import handle_create_tenant
from services.api.routes.upload import handle_csv_upload_url, handle_get_job_status
from services.api.source_collection import CollectionUnavailable

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def route_request(event: dict, store: DynamoStore) -> dict:
    method = event.get("requestContext", {}).get("http", {}).get("method") or event.get("httpMethod", "GET")
    path = event.get("rawPath") or event.get("path") or "/"

    # Normalize trailing slash
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]

    # Handle CORS preflight options
    if method == "OPTIONS":
        from services.api.response import build_response
        return build_response(204)

    # The caller has no membership yet, so this is the one route that runs before the check below.
    if method == "POST" and path == "/tenants":
        return handle_create_tenant(event, store)

    # All API endpoints require authenticated user with at least viewer role
    auth = authenticate_and_authorize(event, store, allowed_roles=("admin", "analyst", "viewer"))

    # 1. GET /me
    if method == "GET" and path == "/me":
        return handle_get_me(event, store, auth)

    # 2. GET /dashboard/stats
    if method == "GET" and path == "/dashboard/stats":
        return handle_get_dashboard_stats(event, store, auth)

    # 3. GET /alerts
    if method == "GET" and path == "/alerts":
        return handle_get_alerts(event, store, auth)

    # 4. GET /sources
    if method == "GET" and path == "/sources":
        return handle_get_sources(event, store, auth)

    # 5. POST /sources
    if method == "POST" and path == "/sources":
        return handle_post_sources(event, store, auth)

    # 6. PATCH /sources/:id
    if method == "PATCH" and re.match(r"^/sources/[A-Za-z0-9_-]+$", path):
        source_id = path.split("/sources/")[1]
        event.setdefault("pathParameters", {})["source_id"] = source_id
        return handle_patch_source(event, store, auth)

    # 7. DELETE /sources/:id
    if method == "DELETE" and re.match(r"^/sources/[A-Za-z0-9_-]+$", path):
        source_id = path.split("/sources/")[1]
        event.setdefault("pathParameters", {})["source_id"] = source_id
        return handle_delete_source(event, store, auth)

    # 8. POST /jobs or /jobs/csv-upload
    if method == "POST" and path in ("/jobs", "/jobs/csv-upload"):
        return handle_csv_upload_url(event, store, auth)

    # 9. GET /jobs/:id/status
    if method == "GET" and re.match(r"^/jobs/[A-Za-z0-9_-]+/status$", path):
        job_id = path.split("/jobs/")[1].split("/status")[0]
        event.setdefault("pathParameters", {})["job_id"] = job_id
        return handle_get_job_status(event, store, auth)

    # 10. GET /matrix
    if method == "GET" and path == "/matrix":
        return handle_get_matrix(event, store, auth)

    # 11. GET /products or /catalog
    if method == "GET" and path in ("/products", "/catalog"):
        return handle_get_products(event, store, auth)

    if method == "POST" and path in ("/products", "/catalog"):
        return handle_post_catalog(event, store, auth)

    # 12. PATCH /products/:id/match or POST /pending-skus/approve
    if method == "PATCH" and re.match(r"^/products/[A-Za-z0-9_-]+/match$", path):
        product_id = path.split("/products/")[1].split("/match")[0]
        event.setdefault("pathParameters", {})["product_id"] = product_id
        return handle_approve_pending_sku(event, store, auth)

    if method == "GET" and path == "/pending-skus":
        return handle_get_pending_skus(event, store, auth)

    if method == "POST" and path == "/pending-skus/approve":
        return handle_approve_pending_sku(event, store, auth)

    # 13. GET /products/:id/price-history or /history/:id
    if method == "GET" and re.match(r"^/products/[A-Za-z0-9_-]+/price-history$", path):
        product_id = path.split("/products/")[1].split("/price-history")[0]
        event.setdefault("pathParameters", {})["product_id"] = product_id
        return handle_get_history(event, store, auth)

    if method == "GET" and re.match(r"^/history/[A-Za-z0-9_-]+$", path):
        product_id = path.split("/history/")[1]
        event.setdefault("pathParameters", {})["product_id"] = product_id
        return handle_get_history(event, store, auth)

    # 14. GET /reports
    if method == "GET" and path == "/reports":
        return handle_get_reports(event, store, auth)

    # 15. GET /reports/:id
    if method == "GET" and re.match(r"^/reports/[A-Za-z0-9_-]+$", path):
        report_id = path.split("/reports/")[1]
        event.setdefault("pathParameters", {})["report_id"] = report_id
        return handle_get_report_detail(event, store, auth)

    if method == "POST" and path == "/reports/trigger":
        return handle_post_trigger_report(event, store, auth)

    # 16. GET /members
    if method == "GET" and path == "/members":
        return handle_get_members(event, store, auth)

    # 17. POST /members/invite
    if method == "POST" and path == "/members/invite":
        return handle_invite_member(event, store, auth)

    # 18. PATCH /members/:id
    if method == "PATCH" and re.match(r"^/members/[A-Za-z0-9_-]+$", path):
        user_id = path.split("/members/")[1]
        event.setdefault("pathParameters", {})["user_id"] = user_id
        return handle_patch_member(event, store, auth)

    return error("not_found", f"Route {method} {path} not found", 404)


def handler(event: dict, context=None, store: DynamoStore | None = None) -> dict:
    """AWS Lambda Entry Point for API Gateway HTTP API."""
    if store is None:
        store = DynamoStore()

    try:
        return route_request(event, store)
    except AccessDenied as exc:
        return error("access_denied", str(exc), 403)
    except QuotaExceeded as exc:
        return error("quota_exceeded", str(exc), 429)
    except Conflict as exc:
        return error("conflict", str(exc), 409)
    except InvalidRecord as exc:
        return error("invalid_request", str(exc), 400)
    except CollectionUnavailable as exc:
        return error(
            str(exc),
            "Nguồn đã lưu nhưng chưa gửi được yêu cầu thu thập. Hãy thử lại."
            if exc.source_saved else "Chưa cấu hình hàng đợi thu thập. Cần cập nhật backend và template.",
            503, {"source_saved": exc.source_saved, **({"job_id": exc.job_id} if exc.job_id else {})},
        )
    except Exception:
        logger.exception("Internal error processing request")
        return error("internal_server_error", "An unexpected error occurred", 500)
