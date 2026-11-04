"""Create isolated fixtures and check the deployed CSV path and duplicate delivery."""

import argparse
import csv
import io
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError

from marketdata.repository import DynamoStore
from scripts.verify_aws import get_stack, new_session, write_report

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COUNTS = {"accepted": 90, "pending": 0, "rejected": 10}


def csv_fixture(now):
    with (ROOT / "tests/fixtures/prices.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        row["observed_at"] = (now - timedelta(minutes=5)).isoformat()
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def wait_for_job(store, tenant, job_id, timeout, sleep=time.sleep, clock=time.monotonic):
    deadline = clock() + timeout
    while clock() < deadline:
        job = store.get(tenant, "JOB#" + job_id)
        if job and job.get("status") == "failed":
            raise RuntimeError("csv_job_failed: " + str(job.get("error", "see CloudWatch")))
        if job and job.get("status") == "completed":
            return job
        sleep(5)
    raise TimeoutError("csv_job_did_not_complete")


def check_result(store, tenant_a, tenant_b, job):
    counts = job.get("counts", {})
    observations_a = len(list(store.product_history(tenant_a, "phone")))
    observations_b = len(list(store.product_history(tenant_b, "phone")))
    if counts != EXPECTED_COUNTS or observations_a != 90 or observations_b != 0:
        raise RuntimeError(
            f"unexpected_csv_result: counts={counts}, a={observations_a}, b={observations_b}"
        )
    return {"counts": counts, "tenant_a_observations": observations_a,
            "tenant_b_observations": observations_b}


def replay_csv(lambda_client, function_arn, bucket, key, version):
    """Replay the same version through the deployed extractor; do not PUT a new version."""
    event = {
        "Records": [{"messageId": "smoke-retry-" + uuid.uuid4().hex,
                     "body": json.dumps({
                         "source": "aws.s3", "detail-type": "Object Created",
                         "detail": {"bucket": {"name": bucket},
                                    "object": {"key": key, "version-id": version}},
                     })}],
    }
    return invoke_batch(lambda_client, function_arn, event)


def invoke_batch(lambda_client, function_arn, event):
    response = lambda_client.invoke(
        FunctionName=function_arn, InvocationType="RequestResponse",
        Payload=json.dumps(event).encode("utf-8"),
    )
    with response["Payload"] as stream:
        result = json.loads(stream.read())
    if (response.get("FunctionError") or not isinstance(result, dict)
            or result.get("batchItemFailures") != []):
        raise RuntimeError("worker_replay_failed")
    return result


def replay_normalizer(lambda_client, function_arn, tenant, job_id, content):
    """Check all duplicate rows synchronously, without relying on SQS queue-depth estimates."""
    rows = list(csv.DictReader(io.StringIO(content.decode("utf-8"))))
    records = [{"messageId": "smoke-row-" + str(number), "body": json.dumps({
        "schema_version": 1, "tenant_id": tenant, "source_id": "vendor", "job_id": job_id,
        "row_number": number, "record": row,
    })} for number, row in enumerate(rows, 1)]
    for offset in range(0, len(records), 10):
        invoke_batch(lambda_client, function_arn, {"Records": records[offset:offset + 10]})
    return {"records": len(records), "batch_failures": 0}


def check_dlq(session, outputs, report, timeout=1800, clock=time.monotonic, output_path=None):
    """Send one identifiable invalid envelope; leave the DLQ message for inspection."""
    sqs = session.client("sqs")
    queue = outputs["TransformQueueUrl"]
    attrs = sqs.get_queue_attributes(QueueUrl=queue, AttributeNames=["RedrivePolicy", "VisibilityTimeout"])["Attributes"]
    policy = json.loads(attrs["RedrivePolicy"])
    if int(policy["maxReceiveCount"]) != 5:
        raise RuntimeError("unexpected_transform_redrive_count")
    arn = policy["deadLetterTargetArn"]
    account, name = arn.split(":")[-2:]
    dlq = sqs.get_queue_url(QueueName=name, QueueOwnerAWSAccountId=account)["QueueUrl"]
    marker = "smoke-poison-" + uuid.uuid4().hex
    body = json.dumps({"schema_version": 999, "smoke_marker": marker})
    sent = sqs.send_message(QueueUrl=queue, MessageBody=body)
    report["dlq_test"] = {"marker": marker, "message_id": sent["MessageId"], "queue_url": queue,
                          "dlq_url": dlq, "configured_max_receive_count": 5,
                          "visibility_timeout": int(attrs["VisibilityTimeout"])}
    if output_path:
        write_report(report, output_path)
    print(f"Waiting for own marker in TransformQueue DLQ (up to {timeout}s)...", flush=True)
    deadline = clock() + timeout
    while clock() < deadline:
        messages = sqs.receive_message(
            QueueUrl=dlq, MaxNumberOfMessages=10, WaitTimeSeconds=20, VisibilityTimeout=0,
            MessageSystemAttributeNames=["ApproximateReceiveCount"],
        ).get("Messages", [])
        for message in messages:
            if message["Body"] == body:
                report["dlq_test"].update({"observed_at": datetime.now(UTC).isoformat(),
                                           "dlq_message_id": message["MessageId"],
                                           "receive_count": message.get("Attributes", {}).get("ApproximateReceiveCount"),
                                           "message_retained": True})
                return
        # Messages were received with visibility 0; unrelated messages are never hidden/deleted.
        if messages:
            time.sleep(2)
    raise TimeoutError("own_marker_not_observed_in_dlq")


def run_smoke_test(session, stack_name, report, timeout=360, check_dead_letter=False,
                   dlq_timeout=1800, output_path=None):
    stack, _ = get_stack(session.client("cloudformation"), stack_name)
    if stack["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
        raise RuntimeError("stack_not_ready: " + stack["StackStatus"])
    outputs = {x["OutputKey"]: x["OutputValue"] for x in stack.get("Outputs", [])}
    report["account_id"] = session.client("sts").get_caller_identity()["Account"]
    report["stack_status"] = stack["StackStatus"]
    store = DynamoStore(outputs["ControlTable"], outputs["ObservationsTable"],
                        resource=session.resource("dynamodb"), client=session.client("dynamodb"))
    suffix = uuid.uuid4().hex[:12]
    tenant_a, tenant_b, job_id = f"smoke-a-{suffix}", f"smoke-b-{suffix}", f"csv-{suffix}"
    key = f"tenants/{tenant_a}/raw/{job_id}"
    report["fixtures"] = {"tenant_a": tenant_a, "tenant_b": tenant_b, "job_id": job_id,
                          "raw_bucket": outputs["RawBucket"], "raw_key": key,
                          "control_table": outputs["ControlTable"],
                          "observations_table": outputs["ObservationsTable"]}
    for tenant in (tenant_a, tenant_b):
        store.put(tenant, "META", {"status": "active", "product_count": 0})
        store.put(tenant, "SOURCE#vendor", {"source_id": "vendor", "kind": "csv", "enabled": True})
        store.create_product(tenant, "phone", "Phone smoke test")
        store.set_mapping(tenant, "vendor", "001-PHONE", "phone")
    store.create_job(tenant_a, job_id,
                     {"source_id": "vendor", "kind": "csv", "status": "queued", "raw_key": key})
    s3 = session.client("s3")
    content = csv_fixture(datetime.now(UTC))
    uploaded = s3.put_object(Bucket=outputs["RawBucket"], Key=key,
                             Body=content, ContentType="text/csv")
    version = uploaded.get("VersionId")
    if not version or version == "null":
        raise RuntimeError("raw_bucket_versioning_required")
    report["fixtures"]["raw_version"] = version
    job = wait_for_job(store, tenant_a, job_id, timeout)
    report["first_delivery"] = check_result(store, tenant_a, tenant_b, job)
    report["retry_response"] = replay_csv(session.client("lambda"), outputs["CSVWorkerArn"],
                                          outputs["RawBucket"], key, version)
    report["normalizer_replay"] = replay_normalizer(
        session.client("lambda"), outputs["NormalizerArn"], tenant_a, job_id, content)
    job = wait_for_job(store, tenant_a, job_id, timeout)
    report["after_replay"] = check_result(store, tenant_a, tenant_b, job)
    if report["after_replay"] != report["first_delivery"]:
        raise RuntimeError("duplicate_delivery_changed_results")
    report["status"] = "passed"
    report["checks"] = {"s3_event_to_normalizer": "passed", "duplicate_csv_delivery": "passed",
                        "tenant_partition_result": "passed", "api_authorization": "not_run",
                        "cognito_login": "not_run", "api_web_sources": "not_run",
                        "dlq_redrive": "not_run", "notifications": "not_run"}
    report["retry_method"] = "same_csv_version_replay_and_all_rows_replayed_to_deployed_normalizer"
    report["cleanup"] = (
        "Fixtures remain for inspection. Delete only the two listed tenant partitions from both "
        "tables and the listed S3 object version after recording evidence. Do not empty buckets."
    )
    if check_dead_letter:
        report["status"] = "running"
        report["checks"]["dlq_redrive"] = "running"
        if output_path:
            write_report(report, output_path)
        check_dlq(session, outputs, report, dlq_timeout, output_path=output_path)
        report["checks"]["dlq_redrive"] = "passed"
        report["status"] = "passed"
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.getenv("AWS_PROFILE", "marketdata-dev"))
    parser.add_argument("--region", default=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
    parser.add_argument("--stack", default="marketdata-hoang-dev")
    parser.add_argument("--write-fixtures", "--new", action="store_true",
                        help="Allow live DynamoDB/S3 fixtures and a deployed Lambda replay")
    parser.add_argument("--timeout", type=int, default=360, help="Seconds to wait per CSV delivery")
    parser.add_argument("--check-dlq", action="store_true",
                        help="Also send one invalid envelope and wait for its real DLQ redrive")
    parser.add_argument("--dlq-timeout", type=int, default=1800,
                        help="Seconds to wait for DLQ redrive; queue visibility stays unchanged")
    parser.add_argument("--output", type=Path, default=Path("build/evidence/csv-smoke.json"))
    args = parser.parse_args(argv)
    if not args.write_fixtures:
        parser.error("Live writes require --write-fixtures. For a read-only check use scripts.verify_aws.")
    if not 30 <= args.timeout <= 1800:
        parser.error("--timeout must be between 30 and 1800 seconds")
    if not 30 <= args.dlq_timeout <= 3600:
        parser.error("--dlq-timeout must be between 30 and 3600 seconds")
    report = {"status": "failed", "checked_at": datetime.now(UTC).isoformat(), "mode": "live_aws",
              "stack_name": args.stack, "region": args.region}
    code = 1
    try:
        run_smoke_test(new_session(args.profile, args.region), args.stack, report, args.timeout,
                       args.check_dlq, args.dlq_timeout, args.output)
        code = 0
    except (BotoCoreError, ClientError, RuntimeError, ValueError, KeyError, TimeoutError) as exc:
        report["status"] = "failed"
        report["error"] = {"type": type(exc).__name__, "code": (
            exc.response["Error"]["Code"] if isinstance(exc, ClientError) else str(exc)
        )}
    write_report(report, args.output)
    print(json.dumps(report, ensure_ascii=False, default=str, indent=2))
    print(f"Evidence: {args.output}")
    return code


if __name__ == "__main__":
    sys.exit(main())
