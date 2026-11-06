import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config

from app.core.config import get_settings


@dataclass(frozen=True)
class MensajeSqs:
    """Un mensaje recibido: el cuerpo tal cual (texto) y lo que SQS informa de su entrega."""

    message_id: str
    receipt_handle: str
    cuerpo: str
    group_id: str
    recepciones: int  # cuántas veces se ha entregado (1 la primera); al pasar el máximo la cola lo manda a la DLQ


class SqsQueueClient:
    """Envío y recepción en una cola SQS FIFO. Credenciales: cadena por defecto de boto3 (rol IAM, perfil, etc.)."""

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

    def receive(self, max_messages: int = 10, wait_seconds: int = 10) -> list[MensajeSqs]:
        """Long polling: espera hasta `wait_seconds` a que haya mensajes. Mantiene el orden dentro de cada grupo."""
        response = self._client.receive_message(
            QueueUrl=self._queue_url,
            MaxNumberOfMessages=max_messages,
            WaitTimeSeconds=wait_seconds,
            AttributeNames=["ApproximateReceiveCount", "MessageGroupId"],
        )
        return [
            MensajeSqs(
                message_id=m["MessageId"],
                receipt_handle=m["ReceiptHandle"],
                cuerpo=m["Body"],
                group_id=m.get("Attributes", {}).get("MessageGroupId", ""),
                recepciones=int(m.get("Attributes", {}).get("ApproximateReceiveCount", "1")),
            )
            for m in response.get("Messages", [])
        ]

    def delete(self, receipt_handle: str) -> None:
        """Se llama solo cuando el mensaje ya se procesó."""
        self._client.delete_message(QueueUrl=self._queue_url, ReceiptHandle=receipt_handle)

    def change_visibility(self, receipt_handle: str, seconds: int) -> None:
        """Cuándo vuelve a ser visible: `0` lo libera ya; un valor mayor aplaza el reintento (backoff)."""
        self._client.change_message_visibility(
            QueueUrl=self._queue_url, ReceiptHandle=receipt_handle, VisibilityTimeout=seconds
        )

    def visibility_timeout(self) -> int:
        """Tiempo que un mensaje recibido queda oculto para los demás consumidores."""
        atributos = self._client.get_queue_attributes(QueueUrl=self._queue_url, AttributeNames=["VisibilityTimeout"])
        return int(atributos["Attributes"]["VisibilityTimeout"])


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
