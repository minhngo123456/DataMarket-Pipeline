"""Handler for /catalog and /products endpoints."""

import json

from boto3.dynamodb.conditions import Key

from marketdata.access import create_product
from marketdata.repository import DynamoStore, pk
from services.api.auth import AuthContext
from services.api.response import error, ok, paginated


def handle_get_catalog(event: dict, store: DynamoStore, auth: AuthContext) -> dict:
    """List all products in the tenant's catalog (compat handler)."""
    return handle_get_products(event, store, auth)


def handle_get_products(event: dict, store: DynamoStore, auth: AuthContext) -> dict:
    """List products with optional filter by match_status (confirmed, pending, rejected, all)."""
    store.tenant(auth.tenant_id)
    query_params = event.get("queryStringParameters") or {}
    match_status = query_params.get("match_status", "all")

    items = []

    # 1. Fetch confirmed products from catalog
    if match_status in ("all", "confirmed"):
        products = list(store.pages(
            store.control,
            KeyConditionExpression=Key("pk").eq(pk(auth.tenant_id)) & Key("sk").begins_with("PRODUCT#"),
            ConsistentRead=True,
        ))
        for p in products:
            items.append({
                "product_id": p["product_id"],
                "name": p.get("name"),
                "attributes": p.get("attributes", {}),
                "match_status": "confirmed",
            })

    # 2. Fetch pending or rejected receipts
    if match_status in ("all", "pending", "rejected"):
        status_filter = None if match_status == "all" else match_status
        receipts = store.receipts(auth.tenant_id, status=status_filter)
        for r in receipts:
            if match_status == "all" and r["status"] == "accepted":
                continue  # already in products catalog
            items.append({
                "observation_id": r["observation_id"],
                "product_id": r.get("product_id"),
                "source_sku": r.get("source_sku"),
                "price": r.get("price"),
                "currency": r.get("currency"),
                "match_status": r["status"],
                "source_id": r.get("source_id"),
                "reason": r.get("reason"),
            })

    return paginated(
        data=items,
        total=len(items),
        page=1,
        limit=20,
        extra={"tenant_id": auth.tenant_id, "products": items, "count": len(items)},
    )


def handle_post_catalog(event: dict, store: DynamoStore, auth: AuthContext) -> dict:
    """Create a new product in the catalog (enforcing 100 SKU limit & role checks)."""
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return error("invalid_json", "Request body must be valid JSON", 400)

    product_id = body.get("product_id")
    name = body.get("name")
    attributes = body.get("attributes", {})

    if not product_id or not name:
        return error("missing_required_fields", "product_id and name are required", 400)

    # create_product checks role ('admin', 'analyst') and executes DynamoDB transaction
    create_product(store, auth.user_id, product_id, name, attributes)

    return ok(
        {
            "tenant_id": auth.tenant_id,
            "product_id": product_id,
            "name": name,
            "attributes": attributes,
            "status": "created",
        },
        status_code=201,
    )
