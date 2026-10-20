import logging
from typing import Annotated, Any

from fastapi import Depends
from fastapi.concurrency import run_in_threadpool

from app.core.logging import mask_phone
from app.infrastructure.sqs_queue_client import SqsQueueClient, get_sqs_queue_client
from app.modules.webhook.payload_adapter import WhatsAppPayloadAdapter

logger = logging.getLogger(__name__)


class WebhookService:
    """Normaliza el payload de WhatsApp y encola cada mensaje en SQS FIFO."""

    def __init__(self, queue: SqsQueueClient) -> None:
        self._queue = queue

    async def enqueue_payload(self, payload: dict[str, Any]) -> int:
        """Encola los mensajes del payload y devuelve cuántos se enviaron.

        Si SQS falla, la excepción se propaga para que el router responda 500 y Meta reintente;
        MessageDeduplicationId = wamid evita duplicar los que ya se habían enviado.
        """
        statuses = WhatsAppPayloadAdapter.count_statuses(payload)
        if statuses:
            logger.debug("Eventos de estado ignorados", extra={"statuses": statuses})

        messages = WhatsAppPayloadAdapter.extract_messages(payload)
        for message in messages:
            # boto3 es síncrono: se ejecuta en un hilo para no bloquear el event loop.
            sqs_message_id = await run_in_threadpool(
                self._queue.send_fifo,
                WhatsAppPayloadAdapter.to_queue_body(message),
                message["from_"],  # grupo = celular: conserva el orden por conversación
                message["wamid"],  # deduplicación: Meta reintenta con el mismo id
            )
            logger.info(
                "Mensaje encolado",
                extra={
                    "wamid": message["wamid"],
                    "celular": mask_phone(message["from_"]),
                    "tipo": message["type"],
                    "sqs_message_id": sqs_message_id,
                },
            )
        return len(messages)


def get_webhook_service(
    queue: Annotated[SqsQueueClient, Depends(get_sqs_queue_client)],
) -> WebhookService:
    return WebhookService(queue)
