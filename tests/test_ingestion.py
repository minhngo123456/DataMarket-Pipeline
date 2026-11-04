import json
from copy import deepcopy
from pathlib import Path

import boto3
import pytest

from marketdata.contracts import AccessDenied, InvalidRecord
from marketdata.queries import history
from marketdata.queue import send_rows
from services.ingestion.csv_worker import parse_csv, process_event
from services.ingestion.csv_worker import process_batch as csv_worker_batch
from services.ingestion.dispatcher import dispatch
from services.normalizer.app import process_batch
from tests.conftest import NOW


def upload(store, body):
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="test-raw-bucket")
    s3.put_bucket_versioning(
        Bucket="test-raw-bucket", VersioningConfiguration={"Status": "Enabled"}
    )
    key = "tenants/tenant-a/raw/csv-job"
    result = s3.put_object(Bucket="test-raw-bucket", Key=key, Body=body)
    event = {
        "source": "aws.s3",
        "detail-type": "Object Created",
        "detail": {
            "bucket": {"name": "test-raw-bucket"},
            "object": {"key": key, "version-id": result["VersionId"]},
        },
    }
    sqs = boto3.client("sqs")
    queue = sqs.create_queue(QueueName="transform")["QueueUrl"]
    return s3, sqs, queue, event


def test_100_rows_end_to_end_with_retry(store):
    content = Path("tests/fixtures/prices.csv").read_bytes()
    expected = json.loads(Path("tests/fixtures/expected.json").read_text())
    s3, sqs, queue, event = upload(store, content)
    for _ in range(2):
        process_event(event, store, s3, sqs, "test-raw-bucket", queue)
    processed = 0
    while True:
        messages = sqs.receive_message(QueueUrl=queue, MaxNumberOfMessages=10).get("Messages", [])
        if not messages:
            break
        batch = {"Records": [{"messageId": m["MessageId"], "body": m["Body"]} for m in messages]}
        assert process_batch(batch, store, NOW) == {"batchItemFailures": []}
        for m in messages:
            sqs.delete_message(QueueUrl=queue, ReceiptHandle=m["ReceiptHandle"])
        processed += len(messages)
    assert processed == 200
    assert store.refresh_job("tenant-a", "csv-job") == expected["counts"]
    assert store.get("tenant-a", "JOB#csv-job")["status"] == "completed"
    assert len(history(store, "tenant-a", "phone", NOW)) == 90
    assert not history(store, "tenant-b", "phone", NOW)


def test_csv_duplicate_resuming_after_s3_read_cannot_reopen_completed_job(store):
    content = Path("tests/fixtures/prices.csv").read_bytes()
    s3, sqs, queue, event = upload(store, content)
    completed = {}

    class ConcurrentCsvS3:
        def get_object(self, **kwargs):
            response = s3.get_object(**kwargs)
            # A competing delivery finishes while this delivery still holds its
            # initial queued job read and has not yet returned from GetObject.
            process_event(event, store, s3, sqs, "test-raw-bucket", queue)
            processed = 0
            while messages := sqs.receive_message(
                QueueUrl=queue, MaxNumberOfMessages=10
            ).get("Messages", []):
                batch = {"Records": [
                    {"messageId": message["MessageId"], "body": message["Body"]}
                    for message in messages
                ]}
                assert process_batch(batch, store, NOW) == {"batchItemFailures": []}
                for message in messages:
                    sqs.delete_message(QueueUrl=queue, ReceiptHandle=message["ReceiptHandle"])
                processed += len(messages)
            assert processed == 100
            completed["job"] = store.get("tenant-a", "JOB#csv-job")
            completed["source"] = store.get("tenant-a", "SOURCE#vendor")
            assert completed["job"]["status"] == "completed"
            return response

    process_event(event, store, ConcurrentCsvS3(), sqs, "test-raw-bucket", queue)

    assert store.get("tenant-a", "JOB#csv-job") == completed["job"]
    assert store.get("tenant-a", "SOURCE#vendor") == completed["source"]
    assert not sqs.receive_message(QueueUrl=queue).get("Messages")
    assert len(store.receipts("tenant-a", job_id="csv-job")) == 100
    assert len(history(store, "tenant-a", "phone", NOW)) == 90
    assert not history(store, "tenant-b", "phone", NOW)


def test_csv_retry_repairs_failed_health_write_without_rereading_or_changing_job(store, monkeypatch):
    s3, sqs, queue, event = upload(store, b"wrong,headers\n1,2\n")
    record_health = store.record_source_collection
    failures = []

    def fail_first_failed_health(tenant, source, job_id, kind, status, *args, **kwargs):
        if status == "failed" and not failures:
            failures.append(job_id)
            raise RuntimeError("simulated_failed_health_write_error")
        return record_health(tenant, source, job_id, kind, status, *args, **kwargs)

    monkeypatch.setattr(store, "record_source_collection", fail_first_failed_health)
    message = {
        "messageId": "csv-health-recovery", "body": json.dumps(event),
        "attributes": {"ApproximateReceiveCount": "1"},
    }
    result = csv_worker_batch(
        {"Records": [message]}, store, s3, sqs, "test-raw-bucket", queue
    )

    assert result == {"batchItemFailures": [{"itemIdentifier": "csv-health-recovery"}]}
    failed_job = store.get("tenant-a", "JOB#csv-job")
    assert failed_job["status"] == "failed"
    assert failed_job["error"] == "invalid_csv_headers"
    assert failed_job["raw_version"] == event["detail"]["object"]["version-id"]
    assert failures == ["csv-job"]
    assert store.get("tenant-a", "SOURCE#vendor")["collection_status"] == "running"

    class UnexpectedS3:
        def get_object(self, **kwargs):
            raise AssertionError("a failed job must reconcile health without rereading S3")

    def unexpected_parse(*args, **kwargs):
        raise AssertionError("a failed job must not parse CSV again")

    monkeypatch.setattr("services.ingestion.csv_worker.parse_csv", unexpected_parse)
    message["attributes"]["ApproximateReceiveCount"] = "2"
    retried = csv_worker_batch(
        {"Records": [message]}, store, UnexpectedS3(), sqs, "test-raw-bucket", queue
    )

    assert retried == {"batchItemFailures": []}
    assert store.get("tenant-a", "JOB#csv-job") == failed_job
    health = store.get("tenant-a", "SOURCE#vendor")
    assert health["collection_status"] == "failed"
    assert health["last_job_id"] == "csv-job"
    assert health["last_error_code"] == "invalid_csv_headers"
    assert not sqs.receive_message(QueueUrl=queue).get("Messages")


def test_upload_cannot_switch_job_tenant_or_object_version(store):
    s3, sqs, queue, event = upload(
        store, b"source_sku,price,currency,observed_at\n001-PHONE,1,VND,2026-10-19T00:45:00Z\n"
    )
    process_event(event, store, s3, sqs, "test-raw-bucket", queue)
    changed = deepcopy(event)
    changed["detail"]["object"]["version-id"] = s3.put_object(
        Bucket="test-raw-bucket", Key=event["detail"]["object"]["key"], Body=b"changed"
    )["VersionId"]
    with pytest.raises(AccessDenied, match="version"):
        process_event(changed, store, s3, sqs, "test-raw-bucket", queue)
    changed["detail"]["object"]["key"] = "tenants/tenant-b/raw/only-a-job"
    with pytest.raises(AccessDenied):
        process_event(changed, store, s3, sqs, "test-raw-bucket", queue)


@pytest.mark.parametrize(
    "body",
    [
        b"price\n1\n",
        b"source_sku,price,currency,observed_at\n",
        b"\xff",
        b"source_sku,price,currency,observed_at,tenant_id\n",
    ],
)
def test_bad_file_is_visible_as_failed_job(store, body):
    s3, sqs, queue, event = upload(store, body)
    process_event(event, store, s3, sqs, "test-raw-bucket", queue)
    job = store.get("tenant-a", "JOB#csv-job")
    assert job["status"] == "failed"
    assert job["error"]
    assert not sqs.receive_message(QueueUrl=queue).get("Messages")


def test_csv_limits_and_columns():
    for data in [
        b"x" * (1024 * 1024 + 1),
        b"source_sku,price,currency,observed_at,price\n",
        b"source_sku,price,currency,observed_at\nx,1,VND\n",
        b"source_sku,price,currency,observed_at\nx,1,VND,2026-10-19T00:45:00Z,extra\n",
    ]:
        with pytest.raises(InvalidRecord):
            parse_csv(data)


def test_partial_sqs_send_is_not_silently_ignored():
    class BrokenQueue:
        def send_message_batch(self, **kwargs):
            return {"Failed": [{"Id": "0", "Code": "InternalError"}]}

    with pytest.raises(RuntimeError):
        send_rows(BrokenQueue(), "queue", [{"x": "y"}])


def test_dispatch_uses_active_tenants_enabled_sources_and_stable_ids(store):
    sqs = boto3.client("sqs")
    queues = {
        kind: sqs.create_queue(QueueName=kind)["QueueUrl"] for kind in ("api", "web", "report")
    }
    for tenant in ("tenant-a", "tenant-b"):
        store.put(tenant, "SOURCE#api", {"source_id": "api", "enabled": True, "kind": "api"})
        store.put(
            tenant, "SOURCE#disabled", {"source_id": "disabled", "enabled": False, "kind": "web"}
        )
    event = {"mode": "sources", "scheduled_at": "2026-10-19T00:00:00Z"}
    assert dispatch(event, store, sqs, queues) == {"sent": 2}
    assert dispatch(event, store, sqs, queues) == {"sent": 2}
    messages = sqs.receive_message(QueueUrl=queues["api"], MaxNumberOfMessages=10)["Messages"]
    bodies = [json.loads(m["Body"]) for m in messages]
    assert len({m["job_id"] for m in bodies}) == 2
    assert {m["tenant_id"] for m in bodies} == {"tenant-a", "tenant-b"}
    assert not sqs.receive_message(QueueUrl=queues["web"]).get("Messages")
    assert dispatch(
        {"mode": "reports", "scheduled_at": "2026-10-19T01:00:00Z"}, store, sqs, queues
    ) == {"sent": 2}


def test_query_pagination_does_not_drop_pages():
    class Paginated:
        def query(self, **kwargs):
            if "ExclusiveStartKey" not in kwargs:
                return {"Items": [1], "LastEvaluatedKey": {"pk": "x", "sk": "y"}}
            return {"Items": [2]}

    from marketdata.repository import DynamoStore

    assert list(DynamoStore.pages(Paginated())) == [1, 2]
