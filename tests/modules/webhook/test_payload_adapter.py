from typing import Any

import pytest

from app.modules.webhook.payload_adapter import WhatsAppPayloadAdapter
from tests.payloads import PHONE, image_message, status_event, text_message


def test_extrae_imagen_con_media_id() -> None:
    [message] = WhatsAppPayloadAdapter.extract_messages(image_message())
    assert message["from_"] == PHONE
    assert message["type"] == "image"
    assert message["content"]["media_id"] == "MEDIA123"


def test_extrae_texto() -> None:
    [message] = WhatsAppPayloadAdapter.extract_messages(text_message())
    assert message["content"] == {"text": "Hola"}


def test_statuses_se_ignoran() -> None:
    assert WhatsAppPayloadAdapter.extract_messages(status_event()) == []
    assert WhatsAppPayloadAdapter.count_statuses(status_event()) == 1


def test_payload_vacio_o_sin_entry() -> None:
    assert WhatsAppPayloadAdapter.extract_messages({}) == []
    assert WhatsAppPayloadAdapter.extract_messages({"entry": [{"changes": [{"value": {}}]}]}) == []


def un_mensaje(tipo: str, bloque: dict[str, Any]) -> dict[str, Any]:
    mensaje = {"from": PHONE, "id": f"wamid.{tipo}", "timestamp": "1", "type": tipo, tipo: bloque}
    return {"entry": [{"changes": [{"value": {"messages": [mensaje]}}]}]}


def test_la_ubicacion_compartida_trae_latitud_y_longitud() -> None:
    [m] = WhatsAppPayloadAdapter.extract_messages(
        un_mensaje("location", {"latitude": 5.2712, "longitude": -73.1702, "name": "Finca"})
    )
    assert m["content"] == {"latitud": 5.2712, "longitud": -73.1702, "nombre": "Finca", "direccion": None}


@pytest.mark.parametrize(("tipo", "clave"), [("button_reply", "button_reply"), ("list_reply", "list_reply")])
def test_boton_o_fila_pulsados_traen_el_id_de_la_opcion(tipo: str, clave: str) -> None:
    bloque = {"type": tipo, clave: {"id": "2", "title": "La Esperanza"}}
    [m] = WhatsAppPayloadAdapter.extract_messages(un_mensaje("interactive", bloque))
    assert m["type"] == "interactive" and m["content"] == {"tipo": tipo, "id": "2", "titulo": "La Esperanza"}


def test_boton_de_plantilla_usa_su_payload() -> None:
    [m] = WhatsAppPayloadAdapter.extract_messages(un_mensaje("button", {"payload": "si", "text": "Sí"}))
    assert m["content"] == {"tipo": "button", "id": "si", "titulo": "Sí"}


def test_tipo_no_soportado_conserva_bloque_original() -> None:
    [m] = WhatsAppPayloadAdapter.extract_messages(un_mensaje("reaction", {"emoji": "👍"}))
    assert m["content"] == {"raw": {"emoji": "👍"}}


def test_to_queue_body_usa_from() -> None:
    [message] = WhatsAppPayloadAdapter.extract_messages(text_message())
    body = WhatsAppPayloadAdapter.to_queue_body(message)
    assert body["from"] == PHONE
    assert "from_" not in body
