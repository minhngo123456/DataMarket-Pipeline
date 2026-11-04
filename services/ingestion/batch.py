"""Common SQS batch handling for API and static HTML source workers."""

import json
import logging

from marketdata.contracts import AccessDenied, InvalidRecord, identifier
from services.ingestion.collection import collection_error_code
from services.ingestion.fetch_errors import (
    MAX_RECEIVE_COUNT,
    RetryableFetchError,
    retry_delay_seconds,
)

logger = logging.getLogger(__name__)


def receive_count(message):
    try:
        return max(1, int(message.get("attributes", {}).get("ApproximateReceiveCount", "1")))
    except (TypeError, ValueError):
        return 1


def _mark_job_failed(store, message, source_name, error, require_unbound=False):
    """Fail only the job that owns this queue message; keep its row_count intact."""
    body = json.loads(message["body"])
    tenant_id = identifier(body.get("tenant_id"))
    source_id = identifier(body.get("source_id"))
    job_id = identifier(body.get("job_id"))
    failed = store.fail_source_job(
        tenant_id, job_id, source_id, source_name, error, require_unbound=require_unbound
    )
    if failed:
        store.record_source_collection(tenant_id, source_id, job_id, source_name, "failed", error)
    return failed


def _retry_message(
    message,
    store,
    sqs,
    input_queue_url,
    source_name,
    message_id,
    error,
    require_unbound=False,
):
    count = receive_count(message)
    code = collection_error_code(error, source_name)
    if count >= MAX_RECEIVE_COUNT:
        try:
            _mark_job_failed(
                store,
                message,
                source_name,
                f"retry_limit_exceeded_{code}",
                require_unbound=require_unbound,
            )
        except Exception:
            logger.error("%s_retry_exhaustion_status_update_failed", source_name)
    else:
        try:
            body = json.loads(message["body"])
            store.record_source_collection(
                body["tenant_id"], body["source_id"], body["job_id"], source_name, "retrying", code
            )
        except Exception:
            logger.error("%s_retry_status_update_failed", source_name)
    if count < MAX_RECEIVE_COUNT and input_queue_url and message.get("receiptHandle"):
        try:
            sqs.change_message_visibility(
                QueueUrl=input_queue_url,
                ReceiptHandle=message["receiptHandle"],
                VisibilityTimeout=retry_delay_seconds(error, count),
            )
        except Exception:
            # Returning the partial failure still lets SQS retry using its queue timeout.
            logger.error("%s_retry_delay_update_failed", source_name)

    logger.warning(
        "%s_retryable_failure message_id=%s receive_count=%s reason=%s",
        source_name,
        message_id,
        count,
        code,
    )
    return {"itemIdentifier": message_id}


def process_source_batch(
    event,
    store,
    sqs,
    transform_queue,
    input_queue_url,
    source_name,
    process_event,
    fetcher=None,
):
    """Process SQS records and retry only records that failed transiently."""
    failures = []
    for message in event["Records"]:
        message_id = message["messageId"]
        try:
            body = json.loads(message["body"])
            process_event(body, store, sqs, transform_queue, fetcher=fetcher)
        except RetryableFetchError as exc:
            failures.append(
                _retry_message(
                    message,
                    store,
                    sqs,
                    input_queue_url,
                    source_name,
                    message_id,
                    exc,
                    require_unbound=True,
                )
            )
        except (AccessDenied, InvalidRecord) as exc:
            logger.error(
                "%s_terminal_failure message_id=%s error=%s",
                source_name, message_id, collection_error_code(exc, source_name),
            )
            try:
                _mark_job_failed(
                    store,
                    message,
                    source_name,
                    f"{source_name}_invalid_job_message",
                    require_unbound=True,
                )
            except Exception:
                logger.error("%s_terminal_status_update_failed", source_name)
        except Exception as exc:
            # Treat unhandled infrastructure/worker errors as retryable, but do not
            # persist exception text that could contain a URL or credential.
            logger.error(
                "%s_failed message_id=%s error_type=%s",
                source_name,
                message_id,
                type(exc).__name__,
            )
            retry_error = RetryableFetchError(f"{source_name}_worker_error")
            failures.append(
                _retry_message(
                    message,
                    store,
                    sqs,
                    input_queue_url,
                    source_name,
                    message_id,
                    retry_error,
                )
            )
    return {"batchItemFailures": failures}
