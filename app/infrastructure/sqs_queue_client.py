import json
from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config

from app.core.config import get_settings


class SqsQueueClient:
    """Envío a una cola SQS FIFO. Credenciales: cadena por defecto de boto3 (rol IAM, perfil o variables)."""

    def __init__(self, client: Any, queue_url: str) -> None:
        self._client = client
        self._queue_url = queue_url

    def send_fifo(self, body: dict[str, Any], group_id: str, deduplication_id: str) -> str:
        response = self._client.send_message(
            QueueUrl=self._queue_url,
            MessageBody=json.dumps(body, ensure_ascii=False),
            MessageGroupId=group_id,
            MessageDeduplicationId=deduplication_id,
        )
        message_id: str = response["MessageId"]
        return message_id


def build_boto3_sqs_client() -> Any:
    settings = get_settings()
    return boto3.client(
        "sqs",
        region_name=settings.aws_region,
        endpoint_url=settings.aws_endpoint_url or None,
        config=Config(retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=2, read_timeout=5),
    )


@lru_cache
def get_sqs_queue_client() -> SqsQueueClient:
    """Se cachea para reutilizar el cliente boto3 y su pool de conexiones."""
    return SqsQueueClient(build_boto3_sqs_client(), get_settings().sqs_queue_url)
