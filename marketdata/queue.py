import json


def send_rows(client, queue_url, values):
    """Keep each batch below 240 KiB; inspect partial SendMessageBatch failures."""
    batch, size = [], 0

    def send():
        response = client.send_message_batch(QueueUrl=queue_url, Entries=batch)
        if response.get("Failed"):
            raise RuntimeError("sqs_partial_send_failure")

    for value in values:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        length = len(body.encode("utf-8"))
        if length > 32 * 1024:
            raise ValueError("row_exceeds_32_kib")
        if batch and (len(batch) == 10 or size + length > 240 * 1024):
            send()
            batch, size = [], 0
        batch.append({"Id": str(len(batch)), "MessageBody": body})
        size += length
    if batch:
        send()
