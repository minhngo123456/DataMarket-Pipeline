"""Run source CRUD -> Dispatcher -> SQS -> workers -> Normalizer -> API in Moto.

All AWS services and source responses are simulated. No real Cognito token,
HTTP server, browser, Lambda transport or AWS account is exercised.
"""

import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import boto3
from moto import mock_aws

from marketdata.access import member
from marketdata.contracts import iso
from marketdata.repository import DynamoStore
from scripts.demo_ingestion import create_tables
from services.api.app import handler as api_handler
from services.ingestion.api_worker import process_batch as api_worker
from services.ingestion.dispatcher import handler as dispatcher
from services.ingestion.web_worker import process_batch as web_worker
from services.normalizer.app import process_batch as normalizer

ROOT = Path(__file__).resolve().parents[1]
DEMO_NOW = datetime(2026, 10, 19, 1, 0, tzinfo=UTC)
PRODUCTS = (("001-PHONE", "phone"), ("002-LAPTOP", "laptop"), ("003-TABLET", "tablet"))


def sqs_records(messages):
    return [
        {
            "messageId": message["MessageId"],
            "body": message["Body"],
            "receiptHandle": message["ReceiptHandle"],
            "attributes": message.get("Attributes", {}),
            "eventSource": "aws:sqs",
        }
        for message in messages
    ]


class _LocalPipeline:
    """Only construct inside local_pipeline(), after Moto and network guards start."""

    def __init__(self, now):
        self.now = now
        self.store = DynamoStore()
        self.s3 = boto3.client("s3")
        self.bucket = os.environ["SOURCE_SNAPSHOT_BUCKET"]
        self.s3.create_bucket(Bucket=self.bucket)
        self.s3.put_bucket_versioning(
            Bucket=self.bucket, VersioningConfiguration={"Status": "Enabled"}
        )
        self.sqs = boto3.client("sqs")
        self.queues = {
            kind: self.sqs.create_queue(QueueName="local-source-" + kind)["QueueUrl"]
            for kind in ("api", "web", "transform", "report")
        }
        os.environ.update(
            API_QUEUE_URL=self.queues["api"],
            WEB_QUEUE_URL=self.queues["web"],
            TRANSFORM_QUEUE_URL=self.queues["transform"],
            REPORT_QUEUE_URL=self.queues["report"],
        )
        self.fetch_calls = []
        self.feeds = {}
        self._failed = {kind: {} for kind in ("api", "web")}
        for suffix in ("a", "b"):
            tenant = "tenant-" + suffix
            self.store.put(
                tenant, "META",
                {"status": "active", "product_count": 0, "max_skus": 100, "max_sources": 3},
            )
            for role in ("admin", "analyst", "viewer"):
                user = role + "-" + suffix
                self.store.control.put_item(Item={
                    "pk": "U#" + user, "sk": "MEMBERSHIP",
                    "tenant_id": tenant, "status": "active",
                })
                self.store.put(tenant, "MEMBER#" + user, {"role": role, "status": "active"})

    def api(self, method, path, user="admin-a", body=None, query=None):
        event = {
            "rawPath": path,
            "requestContext": {
                "http": {"method": method},
                # Simulate the authorizer boundary; membership/RBAC remain real.
                "authorizer": {"jwt": {"claims": {"sub": user} if user else {}}},
            },
            "queryStringParameters": query or {},
        }
        if body is not None:
            event["body"] = json.dumps(body)
        clock = self.now

        class FixedClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.astimezone(tz) if tz is not None else clock.replace(tzinfo=None)

        with (
            patch("services.api.routes.matrix.datetime", FixedClock),
            patch("services.api.routes.history.datetime", FixedClock),
            patch("services.api.routes.pending.datetime", FixedClock),
            patch("services.api.routes.sources.datetime", FixedClock),
        ):
            response = api_handler(event, store=self.store)
        return response["statusCode"], json.loads(response["body"])

    def configure_sources(self, user="admin-a"):
        """Create sources/catalog via API; only the known SKU map is a fixture."""
        tenant = member(self.store, user, ("admin", "analyst"))
        for _, product in PRODUCTS:
            status, _ = self.api("POST", "/products", user=user, body={
                "product_id": product, "name": "Fixture " + product,
            })
            assert status == 201, "local_catalog_creation_failed"
        sources = {}
        for kind, source_id, fixture in (
            ("api", "api_supplier", "api_response.json"),
            ("web", "web_competitor", "sample_page.html"),
        ):
            url = f"https://{kind}-{tenant}.example.invalid/prices"
            self.feeds[url] = (ROOT / "tests" / "fixtures" / fixture).read_bytes()
            status, source = self.api("POST", "/sources", user=user, body={
                "source_id": source_id,
                "kind": kind,
                "enabled": True,
                "endpoint" if kind == "api" else "url": url,
                "headers": {"X-Local-Fixture": kind + "-" + tenant},
            })
            assert status == 201, "local_source_creation_failed"
            sources[kind] = source
            for sku, product in PRODUCTS:
                self.store.set_mapping(tenant, source_id, sku, product)
        return sources

    def dispatch(self, at=None):
        return dispatcher({"mode": "sources", "scheduled_at": iso(at or self.now)}, None)

    def fetch(self, url, headers=None):
        # An explicit fixture allowlist prevents accidental source HTTP requests.
        if url not in self.feeds:
            raise RuntimeError("unregistered_local_source_fixture")
        self.fetch_calls.append({"url": url, "headers": dict(headers or {})})
        fixture = self.feeds[url]
        if isinstance(fixture, Exception):
            raise fixture
        if callable(fixture):
            return fixture(url, headers=headers)
        return fixture

    def _receive(self, kind, max_messages=10):
        return self.sqs.receive_message(
            QueueUrl=self.queues[kind], MaxNumberOfMessages=max_messages,
            MessageSystemAttributeNames=["ApproximateReceiveCount"],
            # Moto's visibility clock can still equal the timestamp set by
            # release_retries() on Windows. A bounded long poll lets that clock
            # advance while retaining the same message and receive counter.
            # Keep it above Moto's one-second internal wait so the timeout does
            # not expire before it checks the newly visible message again.
            WaitTimeSeconds=2 if self._failed.get(kind) else 0,
        ).get("Messages", [])

    def run_sources(self, kind, fetcher=None, output_sqs=None, max_messages=10):
        messages = self._receive(kind, max_messages)
        records = sqs_records(messages)
        worker = {"api": api_worker, "web": web_worker}[kind]
        result = worker(
            {"Records": records}, self.store, output_sqs or self.sqs,
            self.queues["transform"], fetcher=fetcher or self.fetch,
            input_queue_url=self.queues[kind],
        )
        failed_ids = {failure["itemIdentifier"] for failure in result["batchItemFailures"]}
        for message in messages:
            message_id = message["MessageId"]
            if message_id in failed_ids:
                self._failed[kind][message_id] = message
            else:
                self.sqs.delete_message(
                    QueueUrl=self.queues[kind], ReceiptHandle=message["ReceiptHandle"]
                )
                self._failed[kind].pop(message_id, None)
        return {
            **result,
            "received": len(messages),
            "bodies": [json.loads(message["Body"]) for message in messages],
            "receive_counts": [int(record["attributes"]["ApproximateReceiveCount"]) for record in records],
        }

    def release_retries(self, kind):
        """Skip waiting in Moto; retain the real message and receive counter."""
        for message in self._failed[kind].values():
            self.sqs.change_message_visibility(
                QueueUrl=self.queues[kind], ReceiptHandle=message["ReceiptHandle"], VisibilityTimeout=0,
            )

    def normalize(self):
        processed = 0
        while messages := self._receive("transform"):
            result = normalizer({"Records": sqs_records(messages)}, self.store, self.now)
            failed = {failure["itemIdentifier"] for failure in result["batchItemFailures"]}
            for message in messages:
                if message["MessageId"] not in failed:
                    self.sqs.delete_message(
                        QueueUrl=self.queues["transform"], ReceiptHandle=message["ReceiptHandle"]
                    )
                    processed += 1
            if failed:
                raise RuntimeError("local_pipeline_normalizer_failed")
        return processed

    def matrix(self, user="admin-a", reference_source="api_supplier"):
        status, result = self.api("GET", "/matrix", user=user, query={
            "reference_source": reference_source, "basis": "tax_included",
        })
        assert status == 200, "local_matrix_read_failed"
        return result

    def job(self, job_id, user="admin-a"):
        status, result = self.api("GET", f"/jobs/{job_id}/status", user=user)
        assert status == 200, "local_job_read_failed"
        return result

    def advance(self, hours):
        self.now += timedelta(hours=hours)


@contextmanager
def local_pipeline(now=DEMO_NOW):
    # Discard inherited AWS credentials/profile/endpoint overrides for this scope.
    environment = {key: value for key, value in os.environ.items() if not key.startswith("AWS_")}
    environment.update(
        AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing",
        AWS_DEFAULT_REGION="us-east-1", AWS_EC2_METADATA_DISABLED="true",
        CONTROL_TABLE="control", OBSERVATIONS_TABLE="observations",
        SOURCE_SNAPSHOT_BUCKET="local-source-pipeline-snapshots",
        DISPATCH_MODE="sources", ALLOW_TEST_AUTH="false",
    )
    with (
        patch.dict(os.environ, environment, clear=True),
        patch("socket.getaddrinfo", side_effect=RuntimeError("network_disabled_in_local_pipeline")),
        patch("socket.socket.connect", side_effect=RuntimeError("network_disabled_in_local_pipeline")),
        mock_aws(),
    ):
        create_tables()
        yield _LocalPipeline(now)


def demo():
    with local_pipeline() as pipeline:
        created = pipeline.configure_sources()
        assert pipeline.dispatch() == {"sent": 2}
        runs = {kind: pipeline.run_sources(kind) for kind in ("api", "web")}
        assert all(run["received"] == 1 and not run["batchItemFailures"] for run in runs.values())
        assert pipeline.normalize() == 6
        jobs = {kind: pipeline.job(run["bodies"][0]["job_id"]) for kind, run in runs.items()}
        assert all(job["status"] == "completed" and job["counts"] == {
            "accepted": 3, "pending": 0, "rejected": 0,
        } for job in jobs.values())
        expected = {
            ("phone", "api_supplier"): 20100000, ("laptop", "api_supplier"): 35000000,
            ("tablet", "api_supplier"): 15000000, ("phone", "web_competitor"): 19900000,
            ("laptop", "web_competitor"): 34500000, ("tablet", "web_competitor"): 14800000,
        }
        matrix = pipeline.matrix()
        assert {(row["product_id"], row["source_id"]): row["price"] for row in matrix["rows"]} == expected
        assert pipeline.matrix(user="admin-b")["rows"] == []
        for kind, source in created.items():
            call = next(call for call in pipeline.fetch_calls if call["url"] == source["url"])
            assert call["headers"] == {"X-Local-Fixture": kind + "-tenant-a"}
        receipts_before = sum(len(pipeline.store.receipts("tenant-a", job_id=job["job_id"])) for job in jobs.values())
        fetches_before = len(pipeline.fetch_calls)
        assert pipeline.dispatch() == {"sent": 2}
        for kind, original in runs.items():
            replay = pipeline.run_sources(kind)
            assert replay["received"] == 1 and not replay["batchItemFailures"]
            assert replay["bodies"][0]["job_id"] == original["bodies"][0]["job_id"]
        assert pipeline.normalize() == 0
        receipts_after = sum(len(pipeline.store.receipts("tenant-a", job_id=job["job_id"])) for job in jobs.values())
        assert receipts_after == receipts_before and len(pipeline.fetch_calls) == fetches_before
        assert pipeline.matrix() == matrix
        report = {
            "mode": "Moto/local; simulated authorizer and source responses; DNS/TCP disabled",
            "synthetic_clock": iso(pipeline.now),
            "sources_created_via_api": len(created),
            "jobs": jobs,
            "matrix_rows": len(matrix["rows"]),
            "tenant_b_matrix_rows": len(pipeline.matrix(user="admin-b")["rows"]),
            "source_fetches_after_replay": len(pipeline.fetch_calls),
            "receipts_before_replay": receipts_before,
            "receipts_after_replay": receipts_after,
            "url_and_fixture_headers_verified": True,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report


if __name__ == "__main__":
    demo()
