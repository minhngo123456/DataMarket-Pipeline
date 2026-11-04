"""Exercise source, report and notification failures on a live stack in a disposable tenant.

Prints the plan and writes nothing unless --yes is passed. --yes creates two Cognito test
users and the test tenant, runs the scenarios (about 30 minutes) and leaves them in place.
--cleanup prints what it would delete, and --cleanup --yes deletes it. Only the fixed test
emails and tenant below are ever written or deleted. The tenant publishes to a topic that
does not exist, so no email is sent. Two CloudWatch alarms go to ALARM on purpose and
return to OK by themselves.

Usage:
    python -m scripts.e2e_failures --stack marketdata-dev --region ap-southeast-1 \
        [--profile marketdata-dev] [--yes] [--cleanup]
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime, timedelta

import boto3
from boto3.dynamodb.conditions import Attr, Key

from marketdata.contracts import iso, stable_id
from marketdata.repository import DynamoStore, pk
from scripts.cleanup_smoke_tenants import key_of, s3_versions, scan_keys
from scripts.e2e_permissions import add_member, api_caller, ensure_user, id_token, user_sub
from scripts.verify_aws import get_stack

TENANT = "e2e-failures"
ADMIN_EMAIL = "e2e-fail-admin@example.invalid"
VIEWER_EMAIL = "e2e-fail-viewer@example.invalid"
TEST_EMAILS = (ADMIN_EMAIL, VIEWER_EMAIL)
HEADER_NAME = "X-Test"
HEADER_VALUE = "dummy-not-a-secret"
MASK = "***"
SSRF_SOURCE, MISSING_SOURCE, UNREACHABLE_SOURCE = "e2e-ssrf", "e2e-404", "e2e-unreachable"
SSRF_URL = "http://169.254.169.254/latest/meta-data/"
UNREACHABLE_URL = "https://e2e-unreachable.invalid/prices.json"
MISSING_PATH = "/demo/e2e-does-not-exist.json"
# Amplify may answer an unknown path with its HTML page instead of a 404; both end the job.
MISSING_ERRORS = ("api_http_error_404", "invalid_json_payload")


def planned_writes():
    return [
        f"Cognito: create users {ADMIN_EMAIL} and {VIEWER_EMAIL} (invitation suppressed, random password)",
        f"ControlTable: put T#{TENANT} / META, with sns_topic_arn pointing at a topic that does not exist",
        f"ControlTable: put the MEMBERSHIP and MEMBER# records of both users in {TENANT}",
        f"API as admin: create sources {SSRF_SOURCE}, {MISSING_SOURCE} (header {HEADER_NAME}), {UNREACHABLE_SOURCE}",
        "ControlTable and APIQueue: one job and one message per source, as the dispatcher does",
        f"API as admin: POST /reports/trigger, which writes a report, a NOTIF# record and one S3 object in {TENANT}",
    ]


def poll(check, timeout, interval=10, sleep=time.sleep, clock=time.monotonic):
    """Call check until it returns something truthy or time runs out; return its last value."""
    deadline = clock() + timeout
    while True:
        value = check()
        if value or clock() >= deadline:
            return value
        sleep(interval)


def terminal_job_problem(job, errors):
    """A job that can never succeed must fail once, with a known reason."""
    if not job or job.get("status") != "failed":
        return f"expected status failed, got {(job or {}).get('status')}"
    if job.get("error") not in errors:
        return f"expected error in {list(errors)}, got {job.get('error')}"
    return None


def retried_job_problem(job):
    if not job or job.get("status") != "failed":
        return f"expected status failed, got {(job or {}).get('status')}"
    if not str(job.get("error")).startswith("retry_limit_exceeded_"):
        return f"expected a retry_limit_exceeded error, got {job.get('error')}"
    return None


def header_problem(payload, source_id, expected):
    source = next((s for s in payload.get("data", []) if s.get("source_id") == source_id), None)
    if source is None:
        return f"source {source_id} not returned"
    value = (source.get("headers") or {}).get(HEADER_NAME)
    return None if value == expected else f"expected {expected!r}, got {value!r}"


def report_problem(report):
    if not report:
        return "no report was created"
    if report.get("ai_status") != "mock_generated":
        return f"expected ai_status mock_generated, got {report.get('ai_status')}"
    if report.get("status") != "ready":
        return f"expected status ready, got {report.get('status')}"
    return None


def notification_problem(record):
    if not record:
        return "no NOTIF record was written"
    if record.get("status") != "failed" or record.get("sns_error") != "sns_publish_failed":
        return f"expected failed/sns_publish_failed, got {record.get('status')}/{record.get('sns_error')}"
    if record.get("sns_sent") or record.get("channels"):
        return "the record claims a channel succeeded"
    return None


def belongs_to_tenant(body):
    try:
        return json.loads(body).get("tenant_id") == TENANT
    except (TypeError, ValueError, AttributeError):
        return False


def tenant_messages(sqs, queue_url, delete=False):
    """Return this tenant's message bodies; every message kept is made visible again at the end."""
    found = {}
    kept = []
    empty_reads = 0
    while empty_reads < 2:
        messages = sqs.receive_message(
            QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=1, VisibilityTimeout=30
        ).get("Messages", [])
        empty_reads = 0 if messages else empty_reads + 1
        for message in messages:
            if belongs_to_tenant(message["Body"]):
                found[message["MessageId"]] = json.loads(message["Body"])
                if delete:
                    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])
                    continue
            kept.append(message["ReceiptHandle"])
    # Still hidden, a message would be missed by a cleanup that runs right after this read.
    for handle in kept:
        sqs.change_message_visibility(QueueUrl=queue_url, ReceiptHandle=handle, VisibilityTimeout=0)
    return list(found.values())


def dead_letter_url(sqs, queue_url):
    attributes = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["RedrivePolicy"])["Attributes"]
    name = json.loads(attributes["RedrivePolicy"])["deadLetterTargetArn"].rsplit(":", 1)[-1]
    return sqs.get_queue_url(QueueName=name)["QueueUrl"]


def queue_depth(sqs, queue_url):
    attributes = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["ApproximateNumberOfMessages"])
    return int(attributes["Attributes"]["ApproximateNumberOfMessages"])


def alarm_state(cloudwatch, stack, logical_id):
    alarms = cloudwatch.describe_alarms(AlarmNamePrefix=f"{stack}-{logical_id}")["MetricAlarms"]
    return alarms[0]["StateValue"] if alarms else None


def unused_topic_arn(stack_id, stack_name):
    """The name the notification function accepts for this tenant, with no topic behind it."""
    _, partition, _, region, account, _ = stack_id.split(":", 5)
    return f"arn:{partition}:sns:{region}:{account}:{stack_name}-tenant-{TENANT}"


def provision(store, cognito, pool_id, unused_topic_arn):
    """Return {email: password} for the test users."""
    admin_sub, admin_password = ensure_user(cognito, pool_id, ADMIN_EMAIL)
    viewer_sub, viewer_password = ensure_user(cognito, pool_id, VIEWER_EMAIL)
    store.put(TENANT, "META", {"status": "active", "product_count": 0, "sns_topic_arn": unused_topic_arn})
    add_member(store, TENANT, admin_sub, "admin", ADMIN_EMAIL)
    add_member(store, TENANT, viewer_sub, "viewer", VIEWER_EMAIL)
    return {ADMIN_EMAIL: admin_password, VIEWER_EMAIL: viewer_password}


def start_job(store, sqs, queue_url, source_id, now):
    job_id = stable_id(TENANT, source_id, now.isoformat())
    body = {
        "schema_version": 1,
        "tenant_id": TENANT,
        "source_id": source_id,
        "job_id": job_id,
        "kind": "api",
        "scheduled_at": iso(now),
    }
    store.create_job(
        TENANT,
        job_id,
        {**body, "status": "queued", "row_count": 0, "expires_at": int((now + timedelta(days=1)).timestamp())},
    )
    sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps(body))
    return job_id


def job_is_final(store, job_id):
    job = store.get(TENANT, "JOB#" + job_id)
    return job if job and job.get("status") in ("failed", "completed") else None


def run_scenarios(store, sqs, s3, cloudwatch, outputs, stack, web_origin, admin, viewer):
    results = []

    def record(name, problem):
        results.append(("FAIL" if problem else "PASS", name, problem or ""))
        print(f"{results[-1][0]} {name}" + (f": {problem}" if problem else ""), flush=True)

    sources = (
        {"source_id": SSRF_SOURCE, "kind": "api", "url": SSRF_URL},
        {
            "source_id": MISSING_SOURCE,
            "kind": "api",
            "url": web_origin.rstrip("/") + MISSING_PATH,
            "headers": {HEADER_NAME: HEADER_VALUE},
        },
        {"source_id": UNREACHABLE_SOURCE, "kind": "api", "url": UNREACHABLE_URL},
    )
    for source in sources:
        status, _ = admin("POST", "/sources", source)
        if status != 201:
            record(f"admin creates source {source['source_id']}", f"expected 201, got {status}")
            return results

    # (d) Header masking.
    record("admin reads the stored header", header_problem(admin("GET", "/sources")[1], MISSING_SOURCE, HEADER_VALUE))
    record("viewer reads the masked header", header_problem(viewer("GET", "/sources")[1], MISSING_SOURCE, MASK))
    status, _ = admin("PATCH", f"/sources/{MISSING_SOURCE}", {"headers": {HEADER_NAME: MASK}})
    record("admin PATCH with the mask returns 200", None if status == 200 else f"expected 200, got {status}")
    record(
        "echoing the mask keeps the stored header",
        header_problem(admin("GET", "/sources")[1], MISSING_SOURCE, HEADER_VALUE),
    )

    # (a) Source failures.
    queue_url = outputs["APIQueueUrl"]
    dlq_url = dead_letter_url(sqs, queue_url)
    now = datetime.now(UTC)
    jobs = {source["source_id"]: start_job(store, sqs, queue_url, source["source_id"], now) for source in sources}

    job = poll(lambda: job_is_final(store, jobs[SSRF_SOURCE]), timeout=180)
    record("metadata URL is blocked without retries", terminal_job_problem(job, ("source_destination_blocked",)))
    job = poll(lambda: job_is_final(store, jobs[MISSING_SOURCE]), timeout=180)
    record("missing URL fails the job without retries", terminal_job_problem(job, MISSING_ERRORS))
    print(f"404 source ended with: {(job or {}).get('error')}", flush=True)

    # (b) and (c): the report succeeds with the mock text, then its notification fails.
    status, trigger = admin("POST", "/reports/trigger", {})
    record("admin triggers a report", None if status == 202 else f"expected 202, got {status}")
    report_job = trigger.get("job_id")

    def first_report():
        return next(iter(admin("GET", "/reports")[1].get("data", [])), None)

    report = poll(first_report, timeout=300)
    record("report is stored with the mock text", report_problem(report))
    if report:
        job = poll(lambda: job_is_final(store, report_job), timeout=60)
        record("report job completes", None if job and job["status"] == "completed" else f"job {job}")
        listed = s3.list_objects_v2(Bucket=outputs["ReportsBucket"], Prefix=f"tenants/{TENANT}/reports/")
        record("report file is in S3", None if listed.get("KeyCount") else "no object under the tenant prefix")
        notif = poll(lambda: store.get(TENANT, "NOTIF#" + report["report_id"]), timeout=180)
        record("failed publish is recorded", notification_problem(notif))
    state = poll(
        lambda: alarm_state(cloudwatch, stack, "NotificationFunctionErrorsAlarm") == "ALARM",
        timeout=720,
        interval=30,
    )
    record("NotificationFunctionErrorsAlarm goes to ALARM", None if state else "alarm did not fire in 12 minutes")

    # (a) continued: the slowest case, five receives with backoff.
    job = poll(lambda: job_is_final(store, jobs[UNREACHABLE_SOURCE]), timeout=1200, interval=30)
    record("unreachable URL fails after the retry limit", retried_job_problem(job))
    arrived = poll(lambda: queue_depth(sqs, dlq_url) > 0, timeout=600, interval=30)
    record("unreachable job reaches the DLQ", None if arrived else "DLQ stayed empty for 10 minutes")
    state = poll(lambda: alarm_state(cloudwatch, stack, "APIQueueDLQAlarm") == "ALARM", timeout=720, interval=30)
    record("APIQueueDLQAlarm goes to ALARM", None if state else "alarm did not fire in 12 minutes")

    dead_jobs = {body.get("job_id") for body in tenant_messages(sqs, dlq_url)}
    unexpected = dead_jobs & {jobs[SSRF_SOURCE], jobs[MISSING_SOURCE]}
    record("terminal failures stay out of the DLQ", f"found {sorted(unexpected)}" if unexpected else None)
    record(
        "the DLQ holds the unreachable job",
        None if jobs[UNREACHABLE_SOURCE] in dead_jobs else f"found {sorted(dead_jobs)}",
    )
    return results


def build_cleanup_plan(store, cognito, pool_id, s3, buckets, sqs, dead_letter_urls):
    subs = {email: user_sub(cognito, pool_id, email) for email in TEST_EMAILS}
    control = [
        key_of(item)
        for item in store.pages(store.control, KeyConditionExpression=Key("pk").eq(pk(TENANT)))
    ]
    memberships = scan_keys(store.control, Attr("sk").eq("MEMBERSHIP") & Attr("tenant_id").eq(TENANT))
    control += [key_of(item) for item in memberships]
    unique_control = list({(key["pk"], key["sk"]): key for key in control}.values())
    observations = scan_keys(
        store.observations,
        Attr("pk").eq(pk(TENANT)) | Attr("pk").begins_with(pk(TENANT) + "#"),
    )
    return {
        "users": [email for email, sub in subs.items() if sub],
        "control": unique_control,
        "observations": [key_of(item) for item in observations],
        "s3_versions": [(bucket, v) for bucket in buckets for v in s3_versions(s3, bucket, TENANT)],
        "dead_letters": {url: len(tenant_messages(sqs, url)) for url in dead_letter_urls},
    }


def delete_cleanup_plan(plan, store, cognito, pool_id, s3, sqs):
    with store.control.batch_writer() as batch:
        for key in plan["control"]:
            batch.delete_item(Key=key)
    with store.observations.batch_writer() as batch:
        for key in plan["observations"]:
            batch.delete_item(Key=key)
    for bucket, version in plan["s3_versions"]:
        s3.delete_object(Bucket=bucket, **version)
    for url in plan["dead_letters"]:
        tenant_messages(sqs, url, delete=True)
    for email in plan["users"]:
        cognito.admin_delete_user(UserPoolId=pool_id, Username=email)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stack", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--yes", action="store_true", help="Write for real; the default only prints the plan")
    parser.add_argument("--cleanup", action="store_true", help="Remove the test users and tenant instead")
    args = parser.parse_args(argv)

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    stack, _ = get_stack(session.client("cloudformation"), args.stack)
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stack["Outputs"]}
    web_origin = next(p["ParameterValue"] for p in stack["Parameters"] if p["ParameterKey"] == "WebOrigin")
    store = DynamoStore(
        outputs["ControlTable"],
        outputs["ObservationsTable"],
        resource=session.resource("dynamodb"),
        client=session.client("dynamodb"),
    )
    cognito = session.client("cognito-idp")
    sqs = session.client("sqs")
    s3 = session.client("s3")
    pool_id, client_id = outputs["UserPoolId"], outputs["UserPoolClientId"]
    dlq_url = dead_letter_url(sqs, outputs["APIQueueUrl"])

    if args.cleanup:
        buckets = [outputs["RawBucket"], outputs["ReportsBucket"]]
        dead_letter_urls = [dead_letter_url(sqs, outputs[name]) for name in ("APIQueueUrl", "TransformQueueUrl", "ReportQueueUrl")]
        plan = build_cleanup_plan(store, cognito, pool_id, s3, buckets, sqs, dead_letter_urls)
        print("DELETING" if args.yes else "DRY RUN: nothing will be deleted")
        print(f"users={plan['users']} control={len(plan['control'])} observations={len(plan['observations'])} "
              f"s3_versions={len(plan['s3_versions'])} dead_letters={sum(plan['dead_letters'].values())}")
        if args.yes:
            delete_cleanup_plan(plan, store, cognito, pool_id, s3, sqs)
            print("Done.")
        return 0

    depth = queue_depth(sqs, dlq_url)
    if depth:
        print(f"[ERROR]: APIQueue DLQ already holds {depth} messages", file=sys.stderr)
        return 2
    print(f"Stack {args.stack}: APIQueue DLQ empty, 404 probe {web_origin.rstrip('/')}{MISSING_PATH}")

    if not args.yes:
        print("DRY RUN: nothing was written. --yes would:")
        for line in planned_writes():
            print("  " + line)
        return 0

    passwords = provision(store, cognito, pool_id, unused_topic_arn(stack["StackId"], args.stack))
    endpoint = outputs["HttpApiEndpoint"]
    admin = api_caller(endpoint, id_token(cognito, pool_id, client_id, ADMIN_EMAIL, passwords[ADMIN_EMAIL]))
    viewer = api_caller(endpoint, id_token(cognito, pool_id, client_id, VIEWER_EMAIL, passwords[VIEWER_EMAIL]))

    results = run_scenarios(
        store, sqs, s3, session.client("cloudwatch"), outputs, args.stack, web_origin, admin, viewer
    )
    counts = {status: sum(r[0] == status for r in results) for status in ("PASS", "FAIL")}
    print(" ".join(f"{status}={count}" for status, count in counts.items()))
    print("Test users and tenant are still in place; remove them with --cleanup --yes.")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
