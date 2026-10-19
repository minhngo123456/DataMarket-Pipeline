import json
import logging
from datetime import UTC, datetime

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
        except Exception:
            # A bad envelope or infrastructure error is retried; bad row values are receipts.
            # Do not log message bodies, credentials or source URLs.
            logger.error("normalizer_failed message_id=%s", message["messageId"])
            failures.append({"itemIdentifier": message["messageId"]})
    return {"batchItemFailures": failures}


def handler(event, context):
    return process_batch(event, DynamoStore(), datetime.now(UTC))
