import os
from collections.abc import Iterator
from typing import Any

# Variables antes de importar la app (Settings se valida al crearla).
os.environ.update(
    {
        "AWS_REGION": "us-east-1",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "SQS_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123456789012/sin-usar.fifo",
        "WHATSAPP_VERIFY_TOKEN": "token-de-prueba",
        "WHATSAPP_APP_SECRET": "secreto-de-prueba",
        "LOG_LEVEL": "DEBUG",
    }
)
os.environ.pop("AWS_ENDPOINT_URL", None)

import boto3  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from moto import mock_aws  # noqa: E402

from app.infrastructure.sqs_queue_client import SqsQueueClient, get_sqs_queue_client  # noqa: E402
from app.main import app  # noqa: E402

APP_SECRET = "secreto-de-prueba"
VERIFY_TOKEN = "token-de-prueba"
QUEUE_NAME = "mensajes-entrantes.fifo"


@pytest.fixture
def sqs() -> Iterator[tuple[Any, str]]:
    """Cola FIFO simulada con moto."""
    with mock_aws():
        client = boto3.client("sqs", region_name="us-east-1")
        queue_url = client.create_queue(
            QueueName=QUEUE_NAME,
            Attributes={"FifoQueue": "true", "ContentBasedDeduplication": "false"},
        )["QueueUrl"]
        yield client, queue_url


@pytest.fixture
def client(sqs: tuple[Any, str]) -> Iterator[TestClient]:
    sqs_client, queue_url = sqs
    app.dependency_overrides[get_sqs_queue_client] = lambda: SqsQueueClient(sqs_client, queue_url)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()
