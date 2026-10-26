"""Handler for /dashboard/stats and /alerts endpoints."""

from datetime import UTC, datetime
from decimal import Decimal

from boto3.dynamodb.conditions import Key

from marketdata.queries import price_matrix
from marketdata.repository import DynamoStore, pk
from services.api.auth import AuthContext
from services.api.response import ok, paginated


def handle_get_dashboard_stats(event: dict, store: DynamoStore, auth: AuthContext) -> dict:
    """Return summary KPI statistics for the dashboard cards."""
    tenant_meta = store.tenant(auth.tenant_id)
    product_count = int(tenant_meta.get("product_count", 0))
    max_skus = min(int(tenant_meta.get("max_skus", 100)), 100)

    # Count sources
    raw_sources = list(store.sources(auth.tenant_id))
    active_sources = sum(1 for s in raw_sources if s.get("enabled", True))

    # Count pending SKU receipts
    pending_receipts = store.receipts(auth.tenant_id, status="pending")

    # Get latest report if any
    reports = list(store.pages(
        store.control,
        KeyConditionExpression=Key("pk").eq(pk(auth.tenant_id)) & Key("sk").begins_with("REPORT#"),
        ConsistentRead=True,
    ))
    latest_report = None
    if reports:
        # Sort by created_at or period
        sorted_reports = sorted(reports, key=lambda r: r.get("created_at", ""), reverse=True)
        top = sorted_reports[0]
        latest_report = {
            "report_id": top.get("report_id") or top["sk"].removeprefix("REPORT#"),
            "date": top.get("period"),
            "status": top.get("status"),
            "ai_status": top.get("ai_status"),
            "summary_preview": (top.get("summary") or "")[:200],
        }

    return ok({
        "total_skus": product_count,
        "max_skus": max_skus,
        "total_sources": len(raw_sources),
        "active_sources": active_sources,
        "pending_skus": len(pending_receipts),
        "error_count_24h": 0,
        "latest_report": latest_report,
    })


def handle_get_alerts(event: dict, store: DynamoStore, auth: AuthContext) -> dict:
    """Return active alerts: price anomalies (>15% deviation) and pending unmapped SKUs."""
    now = datetime.now(UTC)
    alerts = []

    # 1. Price anomalies in the last 24h
    matrix = price_matrix(store, auth.tenant_id, now=now, reference_source="internal", basis="tax_included")
    for row in matrix.get("rows", []):
        change = row.get("change_percent")
        if change is not None and abs(change) >= Decimal("15.00"):
            alerts.append({
                "id": f"alert-anomaly-{row['product_id']}-{row['source_id']}",
                "type": "price_anomaly",
                "severity": "warning",
                "title": f"Chênh lệch giá bất thường ở {row['product_id']}",
                "message": f"Nguồn '{row['source_id']}' chênh lệch {change:+}% so với giá nội bộ.",
                "product_id": row["product_id"],
                "source_id": row["source_id"],
                "change_percent": change,
                "created_at": row.get("observed_at"),
            })

    # 2. Pending SKUs needing confirmation
    pending_receipts = store.receipts(auth.tenant_id, status="pending")
    for r in pending_receipts[:10]:
        alerts.append({
            "id": f"alert-pending-{r['observation_id']}",
            "type": "pending_sku",
            "severity": "info",
            "title": f"SKU mới cần xác nhận: {r.get('source_sku')}",
            "message": f"SKU '{r.get('source_sku')}' từ nguồn '{r.get('source_id')}' chưa được map trong catalog.",
            "observation_id": r["observation_id"],
            "created_at": r.get("observed_at"),
        })

    return paginated(data=alerts, total=len(alerts), page=1, limit=20)
