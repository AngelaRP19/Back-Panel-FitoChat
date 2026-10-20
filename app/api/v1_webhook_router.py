import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import PlainTextResponse

from app.api.errors import ApiError
from app.core.config import Settings, get_settings
from app.modules.webhook.service import WebhookService, get_webhook_service
from app.modules.webhook.signature import is_valid_signature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook/whatsapp", tags=["webhook"])


@router.get("", response_class=PlainTextResponse)
def verify_webhook(
    settings: Annotated[Settings, Depends(get_settings)],
    hub_mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    hub_verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    hub_challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> PlainTextResponse:
    """Registro del webhook en Meta: devuelve hub.challenge si el token coincide."""
    expected = settings.whatsapp_verify_token.get_secret_value()
    if hub_mode == "subscribe" and hub_verify_token == expected and hub_challenge is not None:
        logger.info("Webhook verificado por Meta")
        return PlainTextResponse(hub_challenge)
    logger.warning("Verificación del webhook rechazada", extra={"hub_mode": hub_mode})
    raise ApiError(status.HTTP_403_FORBIDDEN, "SIN_PERMISO", "Token de verificación inválido.")


@router.post("")
async def receive_webhook(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[WebhookService, Depends(get_webhook_service)],
) -> Response:
    """Valida la firma, encola los mensajes y responde 200."""
    # La firma se calcula sobre los bytes exactos: se leen y validan antes de parsear el JSON.
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    if not is_valid_signature(body, signature, settings.whatsapp_app_secret.get_secret_value()):
        logger.warning("Firma X-Hub-Signature-256 inválida")
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "NO_AUTENTICADO", "Firma inválida.")

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("Cuerpo con firma válida pero JSON inválido; se ignora")
        return Response(status_code=status.HTTP_200_OK)
    if not isinstance(payload, dict):
        return Response(status_code=status.HTTP_200_OK)

    try:
        enqueued = await service.enqueue_payload(payload)
    except Exception:
        logger.exception("Error enviando mensajes a SQS")
        raise ApiError(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "ERROR_INTERNO", "Ocurrió un error. Intenta de nuevo."
        ) from None

    logger.info("Webhook procesado", extra={"mensajes_encolados": enqueued})
    return Response(status_code=status.HTTP_200_OK)
