import json
import logging
from datetime import UTC, datetime

from marketdata.contracts import SourceGenerationChanged
from marketdata.repository import DynamoStore
from marketdata.service import ingest

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def process_batch(event, store, now):
    failures = []
    for message in event["Records"]:
        try:
            value = json.loads(message["body"])
            result = ingest(value, store, now)
            store.refresh_job(value["tenant_id"], value["job_id"])
            logger.info("row_status=%s message_id=%s", result["status"], message["messageId"])
        except SourceGenerationChanged:
            # Acknowledge obsolete rows only after the old job is terminal.
            # Persistence errors here must retry too; sibling excepts cannot catch them.
            try:
                store.fail_obsolete_source_job(
                    value["tenant_id"], value["job_id"], value["source_id"],
                )
                logger.info("obsolete_source_job message_id=%s", message["messageId"])
            except Exception:
                logger.error("obsolete_source_job_failed message_id=%s", message["messageId"])
                failures.append({"itemIdentifier": message["messageId"]})
        except Exception:
            # A bad envelope or infrastructure error is retried; bad row values are receipts.
            # Do not log message bodies, credentials or source URLs.
            logger.error("normalizer_failed message_id=%s", message["messageId"])
            failures.append({"itemIdentifier": message["messageId"]})
    return {"batchItemFailures": failures}


def handler(event, context):
    return process_batch(event, DynamoStore(), datetime.now(UTC))
