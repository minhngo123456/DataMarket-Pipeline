import json
import os
import urllib.request
from datetime import timedelta
from decimal import Decimal

import pytest

from marketdata.contracts import iso
from scripts.demo_source_pipeline import local_pipeline
from services.ingestion.fetch_errors import RetryableFetchError

EXPECTED_PRICES = {
    ("phone", "api_supplier"): 20100000,
    ("laptop", "api_supplier"): 35000000,
    ("tablet", "api_supplier"): 15000000,
    ("phone", "web_competitor"): 19900000,
    ("laptop", "web_competitor"): 34500000,
    ("tablet", "web_competitor"): 14800000,
}


def test_local_pipeline_blocks_external_http_and_restores_inherited_aws_settings(monkeypatch):
    endpoint = "https://unused-endpoint.example.invalid"
    monkeypatch.setenv("AWS_ENDPOINT_URL", endpoint)
    monkeypatch.setenv("AWS_PROFILE", "unused-local-fixture-profile")
    with local_pipeline() as pipeline:
        assert "AWS_ENDPOINT_URL" not in os.environ
        assert "AWS_PROFILE" not in os.environ
        assert pipeline.api("GET", "/sources")[0] == 200
        with pytest.raises(RuntimeError, match="network_disabled_in_local_pipeline"):
            urllib.request.urlopen("https://must-not-be-contacted.example.invalid", timeout=1)
    assert os.environ["AWS_ENDPOINT_URL"] == endpoint
    assert os.environ["AWS_PROFILE"] == "unused-local-fixture-profile"


def source_url(source):
    return source.get("endpoint") or source["url"]


def run_both_sources(p):
    results = {kind: p.run_sources(kind) for kind in ("api", "web")}
    assert all(not result["batchItemFailures"] for result in results.values())
    p.normalize()
    return results


def matrix_prices(matrix):
    return {(row["product_id"], row["source_id"]): row["price"] for row in matrix["data"]}


def test_sources_created_through_api_reach_jobs_snapshots_and_price_matrix():
    with local_pipeline() as p:
        sources = p.configure_sources()
        assert p.dispatch() == {"sent": 2}
        results = run_both_sources(p)

        assert {kind: result["received"] for kind, result in results.items()} == {
            "api": 1, "web": 1
        }
        assert len(p.fetch_calls) == 2
        assert {
            call["url"]: call["headers"] for call in p.fetch_calls
        } == {source_url(source): source["headers"] for source in sources.values()}
        for kind, result in results.items():
            event = result["bodies"][0]
            assert event["tenant_id"] == "tenant-a"
            assert event["source_id"] == sources[kind]["source_id"]
            job = p.job(event["job_id"])
            assert job["status"] == "completed"
            assert job["row_count"] == 3
            assert job["counts"] == {"accepted": 3, "pending": 0, "rejected": 0}
            stored = p.store.get("tenant-a", "JOB#" + event["job_id"])
            descriptor = stored["source_snapshot"]
            response = p.s3.get_object(
                Bucket=p.bucket, Key=descriptor["key"], VersionId=descriptor["version_id"]
            )
            try:
                snapshot = json.loads(response["Body"].read())
            finally:
                response["Body"].close()
            assert len(snapshot["values"]) == 3
            assert [value["row_number"] for value in snapshot["values"]] == [1, 2, 3]
            assert all(
                value["tenant_id"] == "tenant-a"
                and value["source_id"] == event["source_id"]
                and value["job_id"] == event["job_id"]
                for value in snapshot["values"]
            )
        matrix = p.matrix()
        assert matrix["tenant_id"] == "tenant-a"
        assert matrix["total"] == 6
        assert matrix_prices(matrix) == EXPECTED_PRICES
        expected_changes = {"phone": "-1.00", "laptop": "-1.43", "tablet": "-1.33"}
        for row in matrix["data"]:
            expected = expected_changes[row["product_id"]] if row["source_id"] == "web_competitor" else "0"
            assert Decimal(str(row["change_percent"])) == Decimal(expected)


def test_replaying_same_schedule_keeps_jobs_receipts_snapshots_and_fetch_count():
    with local_pipeline() as p:
        p.configure_sources()
        p.dispatch()
        first = run_both_sources(p)
        job_ids = {kind: result["bodies"][0]["job_id"] for kind, result in first.items()}
        before = {
            kind: p.store.get("tenant-a", "JOB#" + job_id)
            for kind, job_id in job_ids.items()
        }
        prices = matrix_prices(p.matrix())
        assert len(p.store.receipts("tenant-a")) == 6
        assert len(p.fetch_calls) == 2

        assert p.dispatch() == {"sent": 2}
        replay = run_both_sources(p)

        assert {kind: result["bodies"][0]["job_id"] for kind, result in replay.items()} == job_ids
        assert len(p.fetch_calls) == 2
        assert len(p.store.receipts("tenant-a")) == 6
        assert matrix_prices(p.matrix()) == prices
        for kind, job_id in job_ids.items():
            assert p.store.get("tenant-a", "JOB#" + job_id) == before[kind]


def test_patched_api_endpoint_and_headers_apply_to_the_next_scheduled_job():
    with local_pipeline() as p:
        sources = p.configure_sources()
        old_url = source_url(sources["api"])
        original_payload = json.loads(p.feeds[old_url])
        p.dispatch()
        first = run_both_sources(p)
        old_job = first["api"]["bodies"][0]["job_id"]
        p.advance(hours=6)
        for row in original_payload["data"]:
            row["observed_at"] = iso(p.now - timedelta(minutes=15))
        original_payload["data"][0]["price"] = "19000000"
        new_url = "https://updated-api.example.invalid/prices"
        new_headers = {"X-Local-Fixture": "updated-api"}
        p.feeds[new_url] = json.dumps(original_payload).encode()
        status, patched = p.api(
            "PATCH", "/sources/api_supplier",
            body={"endpoint": new_url, "headers": new_headers},
        )
        assert status == 200
        assert patched["endpoint"] == new_url

        assert p.dispatch() == {"sent": 2}
        second = run_both_sources(p)

        assert second["api"]["bodies"][0]["job_id"] != old_job
        assert [call for call in p.fetch_calls if call["url"] == new_url] == [
            {"url": new_url, "headers": new_headers}
        ]
        assert len([call for call in p.fetch_calls if call["url"] == old_url]) == 1
        assert matrix_prices(p.matrix())[("phone", "api_supplier")] == 19000000
        assert p.job(old_job)["status"] == "completed"


def test_disabled_and_deleted_sources_stop_dispatch_without_erasing_history():
    with local_pipeline() as p:
        p.configure_sources()
        p.dispatch()
        run_both_sources(p)
        before = matrix_prices(p.matrix())
        assert p.api("PATCH", "/sources/api_supplier", body={"enabled": False})[0] == 200
        assert p.api("DELETE", "/sources/web_competitor")[0] == 200
        p.advance(hours=6)

        assert p.dispatch() == {"sent": 0}
        assert p.run_sources("api")["received"] == 0
        assert p.run_sources("web")["received"] == 0
        assert len(p.fetch_calls) == 2
        assert matrix_prices(p.matrix()) == before
        status, sources = p.api("GET", "/sources")
        assert status == 200
        assert len(sources["data"]) == 1
        assert sources["data"][0]["source_id"] == "api_supplier"
        assert sources["data"][0]["enabled"] is False


def test_same_source_ids_are_tenant_scoped_and_body_cannot_choose_tenant():
    with local_pipeline() as p:
        sources_a = p.configure_sources()
        sources_b = p.configure_sources(user="admin-b")
        # Distinct prices make cross-tenant reads observable even with identical
        # source/product IDs; equal fixtures could conceal a missing tenant filter.
        api_b = json.loads(p.feeds[source_url(sources_b["api"])])
        for row in api_b["data"]:
            row["price"] = str(int(row["price"]) * 2)
        p.feeds[source_url(sources_b["api"])] = json.dumps(api_b).encode()
        web_b = p.feeds[source_url(sources_b["web"])]
        for old_price in (19900000, 34500000, 14800000):
            web_b = web_b.replace(str(old_price).encode(), str(old_price * 2).encode())
        p.feeds[source_url(sources_b["web"])] = web_b
        expected_by_tenant = {
            "tenant-a": EXPECTED_PRICES,
            "tenant-b": {key: price * 2 for key, price in EXPECTED_PRICES.items()},
        }
        assert p.dispatch() == {"sent": 4}
        results = run_both_sources(p)

        assert all(result["received"] == 2 for result in results.values())
        assert {call["url"] for call in p.fetch_calls} == {
            source_url(source) for sources in (sources_a, sources_b) for source in sources.values()
        }
        for user, tenant in (("admin-a", "tenant-a"), ("admin-b", "tenant-b")):
            matrix = p.matrix(user=user)
            assert matrix["tenant_id"] == tenant
            assert matrix_prices(matrix) == expected_by_tenant[tenant]
            own = [body for result in results.values() for body in result["bodies"] if body["tenant_id"] == tenant]
            other = [body for result in results.values() for body in result["bodies"] if body["tenant_id"] != tenant]
            assert len(own) == len(other) == 2
            for event in own:
                assert p.job(event["job_id"], user=user)["status"] == "completed"
            for event in other:
                assert p.api("GET", f'/jobs/{event["job_id"]}/status', user=user)[0] == 404
        status, _ = p.api(
            "POST", "/sources", body={
                "tenant_id": "tenant-b", "source_id": "forged-scope", "kind": "api",
                "endpoint": "https://forged-scope.example.invalid/prices", "enabled": False,
            },
        )
        assert status == 201
        assert p.store.get("tenant-a", "SOURCE#forged-scope") is not None
        assert p.store.get("tenant-b", "SOURCE#forged-scope") is None
        status, matrix = p.api(
            "GET", "/matrix", query={"tenant_id": "tenant-b", "reference_source": "api_supplier"}
        )
        assert status == 200
        assert matrix["tenant_id"] == "tenant-a"
        assert matrix_prices(matrix) == EXPECTED_PRICES


def test_viewer_and_revoked_membership_cannot_mutate_or_read_private_jobs():
    with local_pipeline() as p:
        p.configure_sources()
        p.dispatch()
        results = run_both_sources(p)
        job_id = results["api"]["bodies"][0]["job_id"]
        before = p.store.get("tenant-a", "SOURCE#api_supplier")

        assert p.api("POST", "/sources", user="viewer-a", body={"source_id": "blocked", "kind": "csv"})[0] == 403
        assert p.api("PATCH", "/sources/api_supplier", user="viewer-a", body={"enabled": False})[0] == 403
        assert p.api("DELETE", "/sources/api_supplier", user="viewer-a")[0] == 403
        assert p.api("GET", f"/jobs/{job_id}/status", user="viewer-a")[0] == 403
        assert p.store.get("tenant-a", "SOURCE#api_supplier") == before
        assert matrix_prices(p.matrix(user="viewer-a")) == EXPECTED_PRICES
        status, sources = p.api("GET", "/sources", user="viewer-a")
        assert status == 200
        assert all(value == "***" for source in sources["data"] for value in source.get("headers", {}).values())
        p.store.put("tenant-a", "MEMBER#viewer-a", {"role": "viewer", "status": "inactive"})
        assert p.api("GET", "/matrix", user="viewer-a")[0] == 403


def test_transient_api_failure_retries_actual_sqs_message_while_web_completes():
    with local_pipeline() as p:
        sources = p.configure_sources()
        api_url = source_url(sources["api"])
        payload = p.feeds[api_url]
        p.feeds[api_url] = RetryableFetchError("api_http_retryable_429", retry_after_seconds=1)
        p.dispatch()
        failed = p.run_sources("api")
        web = p.run_sources("web")

        assert failed["received"] == 1
        assert failed["receive_counts"] == [1]
        assert len(failed["batchItemFailures"]) == 1
        api_job = failed["bodies"][0]["job_id"]
        assert p.job(api_job)["status"] == "queued"
        assert not web["batchItemFailures"]
        assert p.normalize() == 3
        assert p.job(web["bodies"][0]["job_id"])["status"] == "completed"
        assert {row["source_id"] for row in p.matrix()["data"]} == {"web_competitor"}
        p.feeds[api_url] = payload
        p.release_retries("api")

        retry = p.run_sources("api")

        assert retry["received"] == 1
        assert retry["receive_counts"] == [2]
        assert not retry["batchItemFailures"]
        assert p.normalize() == 3
        assert p.job(api_job)["counts"] == {"accepted": 3, "pending": 0, "rejected": 0}
        assert p.job(api_job)["status"] == "completed"
        assert matrix_prices(p.matrix()) == EXPECTED_PRICES
        assert len([call for call in p.fetch_calls if call["url"] == api_url]) == 2


def test_invalid_api_payload_fails_only_its_job_and_web_still_reaches_matrix():
    with local_pipeline() as p:
        sources = p.configure_sources()
        p.feeds[source_url(sources["api"])] = b"not-json"
        p.dispatch()
        api = p.run_sources("api")
        web = p.run_sources("web")

        assert not api["batchItemFailures"]
        assert not web["batchItemFailures"]
        assert p.normalize() == 3
        api_job = api["bodies"][0]["job_id"]
        assert p.job(api_job)["status"] == "failed"
        assert p.job(api_job)["row_count"] == 0
        assert p.store.get("tenant-a", "JOB#" + api_job)["error"] == "invalid_json_payload"
        assert p.job(web["bodies"][0]["job_id"])["status"] == "completed"
        assert matrix_prices(p.matrix()) == {
            key: price for key, price in EXPECTED_PRICES.items() if key[1] == "web_competitor"
        }
        assert p.run_sources("api")["received"] == 0


class PartialTransformQueue:
    def __init__(self, sqs):
        self.sqs = sqs

    def __getattr__(self, name):
        return getattr(self.sqs, name)

    def send_message_batch(self, *, QueueUrl, Entries):
        assert len(Entries) == 3
        response = self.sqs.send_message_batch(QueueUrl=QueueUrl, Entries=Entries[:2])
        response["Failed"] = [
            {"Id": Entries[2]["Id"], "Code": "InternalError", "SenderFault": False}
        ]
        return response


def test_partial_transform_send_reuses_snapshot_even_when_api_feed_changes():
    with local_pipeline() as p:
        sources = p.configure_sources()
        api_url = source_url(sources["api"])
        changed_payload = json.loads(p.feeds[api_url])
        p.dispatch()
        first = p.run_sources("api", output_sqs=PartialTransformQueue(p.sqs))
        web = p.run_sources("web")

        assert len(first["batchItemFailures"]) == 1
        assert not web["batchItemFailures"]
        assert p.normalize() == 5
        api_job = first["bodies"][0]["job_id"]
        before = p.store.get("tenant-a", "JOB#" + api_job)
        assert before["status"] == "normalizing"
        assert before["row_count"] == 3
        assert before["counts"] == {"accepted": 2, "pending": 0, "rejected": 0}
        changed_payload["data"] = list(reversed(changed_payload["data"]))[:2]
        changed_payload["data"][0]["price"] = "1"
        p.feeds[api_url] = json.dumps(changed_payload).encode()
        p.release_retries("api")

        retry = p.run_sources("api")

        assert retry["receive_counts"] == [2]
        assert not retry["batchItemFailures"]
        assert p.normalize() == 3
        assert len([call for call in p.fetch_calls if call["url"] == api_url]) == 1
        after = p.store.get("tenant-a", "JOB#" + api_job)
        assert after["source_snapshot"] == before["source_snapshot"]
        assert p.job(api_job)["status"] == "completed"
        assert p.job(api_job)["row_count"] == 3
        assert p.job(api_job)["counts"] == {"accepted": 3, "pending": 0, "rejected": 0}
        assert len(p.store.receipts("tenant-a", job_id=api_job)) == 3
        assert matrix_prices(p.matrix()) == EXPECTED_PRICES
