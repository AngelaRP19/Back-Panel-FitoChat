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


def test_tipo_no_soportado_conserva_bloque_original() -> None:
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": PHONE,
                                    "id": "wamid.LOC",
                                    "timestamp": "1",
                                    "type": "location",
                                    "location": {"latitude": 5.3, "longitude": -73.2},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    [message] = WhatsAppPayloadAdapter.extract_messages(payload)
    assert message["content"] == {"raw": {"latitude": 5.3, "longitude": -73.2}}


def test_to_queue_body_usa_from() -> None:
    [message] = WhatsAppPayloadAdapter.extract_messages(text_message())
    body = WhatsAppPayloadAdapter.to_queue_body(message)
    assert body["from"] == PHONE
    assert "from_" not in body
