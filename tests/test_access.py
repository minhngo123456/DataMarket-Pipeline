import pytest

from marketdata.access import approve_pending, create_product, read_history
from marketdata.contracts import AccessDenied, Conflict
from marketdata.service import ingest
from tests.conftest import NOW


def add_user(store, user, tenant, role):
    store.control.put_item(
        Item={"pk": "U#" + user, "sk": "MEMBERSHIP", "tenant_id": tenant, "status": "active"}
    )
    store.put(tenant, "MEMBER#" + user, {"role": role, "status": "active"})


def test_viewer_cannot_modify_catalog_or_approve(store, message):
    add_user(store, "viewer-a", "tenant-a", "viewer")
    assert read_history(store, "viewer-a", "phone", NOW) == []
    with pytest.raises(AccessDenied):
        create_product(store, "viewer-a", "new", "New")
    message["record"]["source_sku"] = "unknown"
    pending = ingest(message, store, NOW)
    with pytest.raises(AccessDenied):
        approve_pending(store, "viewer-a", pending["observation_id"], "phone", NOW)


def test_user_tenant_is_looked_up_on_server_and_revocation_is_checked(store, message):
    add_user(store, "analyst-a", "tenant-a", "analyst")
    add_user(store, "analyst-b", "tenant-b", "analyst")
    ingest(message, store, NOW)
    assert len(read_history(store, "analyst-a", "phone", NOW)) == 1
    assert read_history(store, "analyst-b", "phone", NOW) == []
    store.put("tenant-a", "MEMBER#analyst-a", {"role": "analyst", "status": "disabled"})
    with pytest.raises(AccessDenied):
        read_history(store, "analyst-a", "phone", NOW)


def test_cannot_approve_to_another_tenants_product(store, message):
    add_user(store, "analyst-a", "tenant-a", "analyst")
    store.create_product("tenant-b", "only-b", "Other tenant")
    message["record"]["source_sku"] = "unknown"
    result = ingest(message, store, NOW)
    with pytest.raises(AccessDenied):
        approve_pending(store, "analyst-a", result["observation_id"], "only-b", NOW)
    with pytest.raises(Conflict, match="expired"):
        from datetime import timedelta

        store.approve_pending(
            "tenant-a", result["observation_id"], "phone", now=NOW + timedelta(days=31)
        )
