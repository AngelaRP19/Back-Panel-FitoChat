"""Estados de los mensajes que enviamos (sent, delivered, read, failed): se leen y quedan en el log."""

import asyncio
import logging
from typing import Any

import pytest

from app.modules.webhook.payload_adapter import WhatsAppPayloadAdapter
from app.modules.webhook.service import WebhookService

FALLIDO = {
    "id": "wamid.SALIDA",
    "status": "failed",
    "recipient_id": "573001112233",
    "errors": [{"code": 131047, "title": "Re-engagement message", "error_data": {"details": "Más de 24 h"}}],
}


def con_estados(*estados: dict[str, Any]) -> dict[str, Any]:
    return {"entry": [{"changes": [{"value": {"statuses": list(estados)}}]}]}


def test_los_estados_de_meta_traen_el_error_de_entrega() -> None:
    leido = {**FALLIDO, "status": "read", "errors": []}
    assert WhatsAppPayloadAdapter.extract_statuses(con_estados(FALLIDO, leido)) == [
        {"wamid": "wamid.SALIDA", "estado": "failed", "destinatario": "573001112233", "codigo_error": 131047,
         "error": "Más de 24 h"},
        {"wamid": "wamid.SALIDA", "estado": "read", "destinatario": "573001112233", "codigo_error": None,
         "error": None},
    ]  # fmt: skip


def test_un_mensaje_no_entregado_queda_en_el_log_sin_el_telefono(caplog: pytest.LogCaptureFixture) -> None:
    class SinCola:
        def send_fifo(self, *_: Any) -> str:
            raise AssertionError("un estado no se encola")

    caplog.set_level(logging.INFO)
    encolados = asyncio.run(WebhookService(SinCola()).enqueue_payload(con_estados(FALLIDO)))  # type: ignore[arg-type]
    assert encolados == 0
    [registro] = [r for r in caplog.records if r.getMessage() == "Meta no entregó un mensaje"]
    assert registro.levelname == "WARNING" and registro.codigo_error == 131047  # type: ignore[attr-defined]
    assert registro.wamid == "wamid.SALIDA" and registro.celular == "***2233"  # type: ignore[attr-defined]
    assert "573001112233" not in caplog.text
