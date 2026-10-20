"""Payloads de ejemplo con la forma que envía WhatsApp Cloud API."""

from typing import Any

PHONE = "573001234567"


def _envelope(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "15550001111", "phone_number_id": "PNID"},
                            **value,
                        },
                    }
                ],
            }
        ],
    }


def image_message(wamid: str = "wamid.IMG001", phone: str = PHONE) -> dict[str, Any]:
    return _envelope(
        {
            "contacts": [{"profile": {"name": "Productor"}, "wa_id": phone}],
            "messages": [
                {
                    "from": phone,
                    "id": wamid,
                    "timestamp": "1760000000",
                    "type": "image",
                    "image": {
                        "id": "MEDIA123",
                        "mime_type": "image/jpeg",
                        "sha256": "abc",
                        "caption": "Hojas con manchas",
                    },
                }
            ],
        }
    )


def text_message(wamid: str = "wamid.TXT001", phone: str = PHONE) -> dict[str, Any]:
    return _envelope(
        {
            "messages": [
                {
                    "from": phone,
                    "id": wamid,
                    "timestamp": "1760000001",
                    "type": "text",
                    "text": {"body": "Hola"},
                }
            ],
        }
    )


def status_event() -> dict[str, Any]:
    return _envelope(
        {
            "statuses": [
                {
                    "id": "wamid.OUT001",
                    "status": "delivered",
                    "timestamp": "1760000002",
                    "recipient_id": PHONE,
                }
            ],
        }
    )
