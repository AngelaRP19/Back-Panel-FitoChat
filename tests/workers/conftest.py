"""Fixtures del consumidor: cola FIFO con DLQ simulada por moto y base de datos real."""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import boto3
import pytest
from moto import mock_aws
from sqlalchemy import Engine, text

from app.infrastructure.sqs_queue_client import SqsQueueClient
from tests.api.conftest import vaciar

MAX_RECEPCIONES = 3  # en AWS son 5; aquí menos para que las pruebas sean cortas


@dataclass
class Colas:
    crudo: Any
    url: str
    url_dlq: str
    cliente: SqsQueueClient

    def encolar(
        self, wamid: str, texto: str = "hola", telefono: str = "573001112233", dedup: str | None = None
    ) -> None:
        """Encola como lo hace el webhook; `dedup` distinto permite repetir un wamid (SQS ya no lo descartaría)."""
        cuerpo = {
            "wamid": wamid,
            "from": telefono,
            "type": "text",
            "timestamp": "1760000000",
            "content": {"text": texto},
        }
        self.crudo.send_message(
            QueueUrl=self.url,
            MessageBody=json.dumps(cuerpo),
            MessageGroupId=telefono,
            MessageDeduplicationId=dedup or wamid,
        )

    def conteo(self, url: str | None = None) -> tuple[int, int]:
        """(visibles, ocultos) de la cola."""
        a = self.crudo.get_queue_attributes(QueueUrl=url or self.url, AttributeNames=["All"])["Attributes"]
        return int(a["ApproximateNumberOfMessages"]), int(a["ApproximateNumberOfMessagesNotVisible"])

    def en_dlq(self) -> list[dict[str, Any]]:
        respuesta = self.crudo.receive_message(QueueUrl=self.url_dlq, MaxNumberOfMessages=10)
        return [json.loads(m["Body"]) for m in respuesta.get("Messages", [])]


@pytest.fixture
def colas() -> Iterator[Colas]:
    with mock_aws():
        crudo = boto3.client("sqs", region_name="us-east-1")
        url_dlq = crudo.create_queue(
            QueueName="mensajes-dlq.fifo", Attributes={"FifoQueue": "true", "ContentBasedDeduplication": "false"}
        )["QueueUrl"]
        arn = crudo.get_queue_attributes(QueueUrl=url_dlq, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        url = crudo.create_queue(
            QueueName="mensajes.fifo",
            Attributes={
                "FifoQueue": "true",
                "ContentBasedDeduplication": "false",
                "VisibilityTimeout": "120",
                "RedrivePolicy": json.dumps({"deadLetterTargetArn": arn, "maxReceiveCount": str(MAX_RECEPCIONES)}),
            },
        )["QueueUrl"]
        yield Colas(crudo, url, url_dlq, SqsQueueClient(crudo, url))


@pytest.fixture
def bd(migrated: Engine) -> Iterator[Engine]:
    vaciar(migrated)
    yield migrated
    vaciar(migrated)


def filas(motor: Engine, sql: str, **parametros: Any) -> list[dict[str, Any]]:
    with motor.connect() as conexion:
        return [dict(f) for f in conexion.execute(text(sql), parametros).mappings()]
