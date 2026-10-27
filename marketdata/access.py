"""Call these helpers with the Cognito sub verified by Hiếu's JWT middleware."""

from marketdata.contracts import AccessDenied, identifier
from marketdata.queries import history, price_matrix


def member(store, verified_user_id, allowed_roles):
    user_id = identifier(verified_user_id)
    membership = store.control.get_item(
        Key={"pk": "U#" + user_id, "sk": "MEMBERSHIP"}, ConsistentRead=True
    ).get("Item")
    if not membership or membership.get("status") != "active":
        raise AccessDenied("membership_unavailable")
    tenant_id = identifier(membership["tenant_id"])
    # Read the tenant-side record too: disabling membership must take effect immediately.
    record = store.get(tenant_id, "MEMBER#" + user_id)
    if not record or record.get("status") != "active" or record.get("role") not in allowed_roles:
        raise AccessDenied("role_not_allowed")
    store.tenant(tenant_id)
    return tenant_id


def create_product(store, verified_user_id, product_id, name, attributes=None):
    tenant = member(store, verified_user_id, ("admin", "analyst"))
    store.create_product(tenant, product_id, name, attributes)


def approve_pending(store, verified_user_id, observation_id, product_id, now):
    tenant = member(store, verified_user_id, ("admin", "analyst"))
    receipt = store.receipt(tenant, observation_id)
    if not receipt:
        raise AccessDenied("pending_row_unavailable")
    store.approve_pending(tenant, observation_id, product_id, now=now)
    store.refresh_job(tenant, receipt["job_id"])


def reject_pending(store, verified_user_id, observation_id, now):
    tenant = member(store, verified_user_id, ("admin", "analyst"))
    receipt = store.receipt(tenant, observation_id)
    if not receipt:
        raise AccessDenied("pending_row_unavailable")
    store.reject_pending(tenant, observation_id, now=now)
    store.refresh_job(tenant, receipt["job_id"])


def read_history(store, verified_user_id, product_id, now, days=30):
    tenant = member(store, verified_user_id, ("admin", "analyst", "viewer"))
    return history(store, tenant, product_id, now, days)


def read_matrix(store, verified_user_id, now, reference_source="internal", basis="tax_included"):
    tenant = member(store, verified_user_id, ("admin", "analyst", "viewer"))
    return price_matrix(store, tenant, now, reference_source, basis)
