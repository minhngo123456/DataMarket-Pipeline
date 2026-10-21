"""Common SQS batch handling for API and static HTML source workers."""

import json
import logging

from marketdata.contracts import AccessDenied, InvalidRecord, identifier
from services.ingestion.fetch_errors import (
    MAX_RECEIVE_COUNT,
    RetryableFetchError,
    retry_delay_seconds,
)

logger = logging.getLogger(__name__)


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
            receive_count = message.get("attributes", {}).get("ApproximateReceiveCount", "1")
            try:
                receive_count = int(receive_count)
            except (TypeError, ValueError):
                receive_count = 1

            if receive_count >= MAX_RECEIVE_COUNT:
                try:
                    body = json.loads(message["body"])
                    tenant_id = identifier(body.get("tenant_id"))
                    job_id = identifier(body.get("job_id"))
                    store.update_job(
                        tenant_id,
                        job_id,
                        status="failed",
                        error=f"retry_limit_exceeded_{exc}",
                        row_count=0,
                    )
                except Exception:
                    logger.exception("%s_retry_exhaustion_status_update_failed", source_name)
            elif input_queue_url and message.get("receiptHandle"):
                try:
                    sqs.change_message_visibility(
                        QueueUrl=input_queue_url,
                        ReceiptHandle=message["receiptHandle"],
                        VisibilityTimeout=retry_delay_seconds(exc, receive_count),
                    )
                except Exception:
                    # Returning the partial failure still lets SQS retry using its queue timeout.
                    logger.exception("%s_retry_delay_update_failed", source_name)

            logger.warning(
                "%s_retryable_failure message_id=%s receive_count=%s reason=%s",
                source_name,
                message_id,
                receive_count,
                exc,
            )
            failures.append({"itemIdentifier": message_id})
        except (AccessDenied, InvalidRecord) as exc:
            logger.error("%s_terminal_failure message_id=%s error=%s", source_name, message_id, exc)
        except Exception:
            logger.error("%s_failed message_id=%s", source_name, message_id, exc_info=True)
            failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures}
