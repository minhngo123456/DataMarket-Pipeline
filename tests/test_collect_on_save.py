import json
from datetime import timedelta
from decimal import Decimal

import pytest

from marketdata.contracts import iso
from scripts.demo_source_pipeline import local_pipeline
from scripts.validate_template import translate
from tests.test_phuc_hy_homepage import HOME, PRODUCTS, fixture_feeds


def create_web(p, **overrides):
    return p.api("POST", "/sources", body={
        "source_id": "phuc-hy", "kind": "web", "url": HOME,
        "price_basis": "tax_included", "collect_now": True, **overrides,
    })


def test_saving_homepage_source_and_reference_collects_without_dispatcher():
    with local_pipeline() as p:
        p.feeds.update(fixture_feeds())
        status, source = create_web(p)
        assert status == 201 and source["collection_status"] == "queued"
        job_id = source["job_id"]
        saved = p.store.get("tenant-a", "SOURCE#phuc-hy")
        queued = p.store.get("tenant-a", "JOB#" + job_id)
        assert saved["collection_status"] == "queued" and saved["last_job_id"] == job_id
        assert "last_attempt_at" not in saved and "collection_started_at" not in queued
        assert queued["source_generation"] == saved["source_generation"]
        assert not p.fetch_calls  # The API only queues; the worker performs HTTP.

        reference_url = "https://internal.example.invalid/prices"
        p.feeds[reference_url] = json.dumps([
            {"source_sku": sku, "price": price + 1000, "currency": "VND",
             "observed_at": iso(p.now - timedelta(minutes=1)), "price_basis": "tax_included"}
            for sku, price in PRODUCTS
        ]).encode()
        assert p.api("POST", "/sources", body={
            "source_id": "internal", "kind": "api", "url": reference_url, "collect_now": True,
        })[0] == 201
        # No Scheduler/Dispatcher invocation anywhere in this test.
        for kind in ("web", "api"):
            run = p.run_sources(kind)
            assert run["received"] == 1 and not run["batchItemFailures"]
        assert len(p.fetch_calls) == 12
        p.advance(hours=1)
        assert p.normalize() == 20
        pending = p.store.receipts("tenant-a", status="pending")
        for index, (sku, _) in enumerate(PRODUCTS):
            product_id = f"product-{index}"
            p.store.create_product("tenant-a", product_id, sku, {})
            for row in pending:
                if row["source_sku"] == sku:
                    p.store.approve_pending("tenant-a", row["observation_id"], product_id, now=p.now)
        matrix = p.matrix(reference_source="internal")["data"]
        assert len(matrix) == 20
        assert all(Decimal(str(row["change_percent"])) < 0 for row in matrix if row["source_id"] == "phuc-hy")
        assert not p.api("GET", "/matrix", user="viewer-b")[1]["data"]


def test_existing_source_can_collect_again_with_patch_and_keeps_configuration():
    with local_pipeline() as p:
        p.feeds.update(fixture_feeds())
        assert create_web(p, collect_now=False)[0] == 201
        before = p.store.get("tenant-a", "SOURCE#phuc-hy")
        status, result = p.api("PATCH", "/sources/phuc-hy", body={"collect_now": True})
        assert status == 200 and result["collection_status"] == "queued"
        after = p.store.get("tenant-a", "SOURCE#phuc-hy")
        assert after["url"] == before["url"] and after["price_basis"] == "tax_included"
        assert after["source_generation"] == before["source_generation"]
        first = p.run_sources("web")
        assert first["received"] == 1 and not first["batchItemFailures"]
        p.advance(hours=0.001)
        status, second = p.api("PATCH", "/sources/phuc-hy", body={"collect_now": True})
        assert status == 200 and second["job_id"] != result["job_id"]
        assert p.run_sources("web")["received"] == 1


@pytest.mark.parametrize("value", ["true", 0, 1, None, [], {}])
@pytest.mark.parametrize("method", ["POST", "PATCH"])
def test_invalid_collect_now_cannot_change_source_or_create_job(value, method):
    with local_pipeline() as p:
        assert create_web(p, collect_now=False)[0] == 201
        before = p.store.get("tenant-a", "SOURCE#phuc-hy")
        if method == "POST":
            status, _ = create_web(p, collect_now=value, name="Must not save")
        else:
            status, _ = p.api("PATCH", "/sources/phuc-hy", body={"collect_now": value, "name": "Must not save"})
        assert status == 400
        assert p.store.get("tenant-a", "SOURCE#phuc-hy") == before
        assert p.run_sources("web")["received"] == 0


@pytest.mark.parametrize("overrides", [{"enabled": False}, {"url": None}, {"kind": "csv"}])
def test_disabled_missing_url_or_csv_sources_cannot_be_queued(overrides):
    with local_pipeline() as p:
        assert create_web(p, **overrides)[0] == 400
        assert p.store.get("tenant-a", "SOURCE#phuc-hy") is None


def test_missing_queue_configuration_is_explicit_and_does_not_save_source(monkeypatch):
    with local_pipeline() as p:
        monkeypatch.delenv("WEB_QUEUE_URL")
        status, result = create_web(p)
        assert status == 503
        assert result["error"]["code"] == "source_collection_not_configured"
        assert result["error"]["details"]["source_saved"] is False
        assert p.store.get("tenant-a", "SOURCE#phuc-hy") is None


def test_queue_failure_is_not_reported_as_success_and_can_be_retried(monkeypatch):
    with local_pipeline() as p:
        class BrokenSQS:
            def send_message(self, **kwargs):
                raise RuntimeError("private-provider-fixture-detail")

        with monkeypatch.context() as scoped:
            scoped.setattr("services.api.source_collection.boto3.client", lambda *_: BrokenSQS())
            status, result = create_web(p)
        assert status == 503 and result["error"]["details"]["source_saved"] is True
        assert "private-provider-fixture-detail" not in json.dumps(result)
        job_id = result["error"]["details"]["job_id"]
        assert p.store.get("tenant-a", "JOB#" + job_id)["status"] == "failed"
        assert p.store.get("tenant-a", "SOURCE#phuc-hy")["last_error_code"] == "source_collection_enqueue_failed"
        p.advance(hours=0.001)
        assert p.api("PATCH", "/sources/phuc-hy", body={"collect_now": True})[0] == 200
        p.feeds.update(fixture_feeds())
        assert not p.run_sources("web")["batchItemFailures"]


def test_queue_health_never_moves_a_running_or_successful_job_backwards():
    with local_pipeline() as p:
        _, source = create_web(p)
        job_id = source["job_id"]
        p.store.record_source_collection("tenant-a", "phuc-hy", job_id, "web", "running", now=p.now)
        running = p.store.get("tenant-a", "SOURCE#phuc-hy")
        assert not p.store.record_source_collection("tenant-a", "phuc-hy", job_id, "web", "queued")
        assert p.store.get("tenant-a", "SOURCE#phuc-hy") == running
        p.feeds.update(fixture_feeds())
        p.run_sources("web")
        success = p.store.get("tenant-a", "SOURCE#phuc-hy")
        assert success["collection_status"] == "succeeded"
        assert not p.store.record_source_collection("tenant-a", "phuc-hy", job_id, "web", "queued")
        assert p.store.get("tenant-a", "SOURCE#phuc-hy") == success


def test_collect_action_respects_rbac_tenant_and_generation(monkeypatch):
    with local_pipeline() as p:
        assert create_web(p, collect_now=False)[0] == 201
        assert p.api("PATCH", "/sources/phuc-hy", user="viewer-a", body={"collect_now": True})[0] == 403
        assert p.api("PATCH", "/sources/phuc-hy", user="analyst-b", body={"tenant_id": "tenant-a", "collect_now": True})[0] == 404
        assert p.run_sources("web")["received"] == 0
        assert p.api("PATCH", "/sources/phuc-hy", body={"collect_now": True})[0] == 200
        assert p.api("DELETE", "/sources/phuc-hy")[0] == 200
        assert create_web(p, collect_now=False)[0] == 201
        p.feeds.update(fixture_feeds())
        assert not p.run_sources("web")["batchItemFailures"]
        assert not p.fetch_calls  # An old generation cannot collect the replacement.


def test_api_template_can_send_only_to_existing_source_and_report_queues():
    resources = translate()["Resources"]
    environment = resources["ApiFunction"]["Properties"]["Environment"]["Variables"]
    assert environment["API_QUEUE_URL"] == {"Ref": "APIQueue"}
    assert environment["WEB_QUEUE_URL"] == {"Ref": "WebQueue"}
    statements = [statement for policy in resources["ApiFunctionRole"]["Properties"]["Policies"]
                  for statement in policy["PolicyDocument"]["Statement"]]
    sends = [statement for statement in statements if "sqs:SendMessage" in statement.get("Action", [])]
    assert len(sends) == 1
    assert sends[0]["Resource"] == [{"Fn::GetAtt": [name, "Arn"]} for name in ("ReportQueue", "APIQueue", "WebQueue")]
