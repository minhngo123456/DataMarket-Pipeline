"""Handler for GET /matrix endpoint."""

from datetime import UTC, datetime

from marketdata.access import read_matrix
from marketdata.repository import DynamoStore
from services.api.auth import AuthContext
from services.api.response import ok


def handle_get_matrix(event: dict, store: DynamoStore, auth: AuthContext) -> dict:
    """Retrieve 24-hour price matrix comparing matched products against a reference source."""
    params = event.get("queryStringParameters") or {}
    reference_source = params.get("reference_source", "internal")
    basis = params.get("basis", "tax_included")

    now = datetime.now(UTC)
    result = read_matrix(
        store,
        auth.user_id,
        now=now,
        reference_source=reference_source,
        basis=basis,
    )
    rows = result.get("rows", [])

    return ok({
        "data": rows,
        "rows": rows,
        "total": len(rows),
        "page": 1,
        "limit": 20,
        "tenant_id": result.get("tenant_id"),
        "window_start": result.get("window_start"),
        "window_end": result.get("window_end"),
        "price_basis": result.get("price_basis"),
    })
