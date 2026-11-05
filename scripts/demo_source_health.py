"""Generate frontend source-health examples with Moto and fixtures; never call AWS."""

import argparse
import json
from pathlib import Path

from scripts.demo_source_pipeline import local_pipeline
from services.api.routes.sources import PUBLIC_COLLECTION_FIELDS
from services.ingestion.fetch_errors import RetryableFetchError


def demo():
    with local_pipeline() as pipeline:
        configured = pipeline.configure_sources()
        samples = {}

        def capture(name):
            status, body = pipeline.api("GET", "/sources", user="viewer-a")
            assert status == 200
            samples[name] = {"status_code": status, "body": body}
            return next(source for source in body["data"] if source["source_id"] == "api_supplier")

        before = capture("before_collection")
        assert "collection_status" not in before
        api_url = configured["api"]["endpoint"]
        valid_payload = pipeline.feeds[api_url]

        def rate_limited_fixture(_url, headers=None):
            assert capture("running")["collection_status"] == "running"
            raise RetryableFetchError("api_http_retryable_429", retry_after_seconds=1)

        pipeline.feeds[api_url] = rate_limited_fixture
        assert pipeline.dispatch() == {"sent": 2}
        first = pipeline.run_sources("api")
        assert first["received"] == 1 and len(first["batchItemFailures"]) == 1
        retrying = capture("retrying")
        assert retrying["collection_status"] == "retrying"
        assert retrying["last_error_code"] == "api_http_retryable_429"

        pipeline.feeds[api_url] = valid_payload
        pipeline.release_retries("api")
        retry = pipeline.run_sources("api")
        web = pipeline.run_sources("web")
        assert retry["received"] == web["received"] == 1
        assert not retry["batchItemFailures"] and not web["batchItemFailures"]
        succeeded = capture("succeeded_before_normalizer")
        assert succeeded["collection_status"] == "succeeded"
        assert "last_error_code" not in succeeded
        assert pipeline.job(succeeded["last_job_id"])["status"] == "normalizing"
        assert pipeline.normalize() == 6
        jobs = {
            "api": pipeline.job(succeeded["last_job_id"]),
            "web": pipeline.job(web["bodies"][0]["job_id"]),
        }
        for job in jobs.values():
            assert job["status"] == "completed"
            assert job["counts"] == {"accepted": 3, "pending": 0, "rejected": 0}
        assert len(pipeline.matrix()["rows"]) == 6
        assert pipeline.matrix(user="viewer-b")["rows"] == []

        status, _ = pipeline.api("PATCH", "/sources/api_supplier", user="analyst-a", body={
            "name": "API supplier renamed locally", "headers": {"X-Local-Fixture": "***"},
        })
        assert status == 200
        after_edit = capture("after_configuration_edit")
        for field in PUBLIC_COLLECTION_FIELDS:
            assert after_edit.get(field) == succeeded.get(field)

        pipeline.advance(hours=6)
        pipeline.feeds[api_url] = RetryableFetchError("api_http_retryable_429")
        assert pipeline.dispatch() == {"sent": 2}
        assert len(pipeline.run_sources("api")["batchItemFailures"]) == 1
        next_retry = capture("next_job_retrying_with_success_history")
        assert next_retry["collection_status"] == "retrying"
        assert next_retry["last_job_id"] != succeeded["last_job_id"]
        assert next_retry["last_success_job_id"] == succeeded["last_success_job_id"]

        pipeline.feeds[api_url] = b"not-json"
        pipeline.release_retries("api")
        failed = pipeline.run_sources("api")
        assert failed["received"] == 1 and not failed["batchItemFailures"]
        terminal = capture("failed_with_success_history")
        assert terminal["collection_status"] == "failed"
        assert terminal["last_error_code"] == "invalid_json_payload"
        for field in ("last_success_at", "last_success_job_id", "last_success_job_at"):
            assert terminal[field] == succeeded[field]

        status, other_tenant = pipeline.api("GET", "/sources", user="viewer-b")
        assert status == 200 and other_tenant["data"] == []
        return {
            "mode": "local-moto-fixtures",
            "live_aws_tested": False,
            "method": "GET", "path": "/sources", "role": "viewer",
            "fixture_clock": "2026-10-19T01:00:00Z",
            "note": "Fixture clock controls observation windows; worker health uses the process clock.",
            "samples": samples,
            "completed_jobs": jobs,
            "matrix_row_count_after_first_jobs": 6,
            "other_tenant": {"status_code": status, "body": other_tenant},
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write example JSON to this local file")
    args = parser.parse_args()
    result = json.dumps(demo(), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result, encoding="utf-8")
        print(f"Saved local-only source health examples: {args.output}")
    else:
        print(result, end="")


if __name__ == "__main__":
    main()
