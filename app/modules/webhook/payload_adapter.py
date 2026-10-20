from typing import Any, TypedDict

# Tipos cuyo contenido es un archivo alojado en Meta (el worker lo descargará con el media id).
MEDIA_TYPES = frozenset({"image", "audio", "video", "document", "sticker"})


class IncomingMessage(TypedDict):
    """Mensaje normalizado que se encola en SQS."""

    wamid: str
    # 'from' es palabra reservada en Python; en el JSON se publica como "from".
    from_: str
    type: str
    timestamp: str | None
    content: dict[str, Any]


class WhatsAppPayloadAdapter:
    """Aísla el formato de WhatsApp Cloud API del resto del sistema."""

    @staticmethod
    def extract_messages(payload: dict[str, Any]) -> list[IncomingMessage]:
        """Recorre entry[].changes[].value.messages[]; los 'statuses' se ignoran."""
        result: list[IncomingMessage] = []
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                for message in value.get("messages") or []:
                    if not message.get("id") or not message.get("from"):
                        continue
                    msg_type = str(message.get("type", "unknown"))
                    result.append(
                        IncomingMessage(
                            wamid=message["id"],
                            from_=message["from"],
                            type=msg_type,
                            timestamp=message.get("timestamp"),
                            content=WhatsAppPayloadAdapter._extract_content(msg_type, message),
                        )
                    )
        return result

    @staticmethod
    def count_statuses(payload: dict[str, Any]) -> int:
        return sum(
            len((change.get("value") or {}).get("statuses") or [])
            for entry in payload.get("entry") or []
            for change in entry.get("changes") or []
        )

    @staticmethod
    def to_queue_body(message: IncomingMessage) -> dict[str, Any]:
        return {
            "wamid": message["wamid"],
            "from": message["from_"],
            "type": message["type"],
            "timestamp": message["timestamp"],
            "content": message["content"],
        }

    @staticmethod
    def _extract_content(msg_type: str, message: dict[str, Any]) -> dict[str, Any]:
        if msg_type == "text":
            return {"text": (message.get("text") or {}).get("body", "")}
        if msg_type in MEDIA_TYPES:
            media = message.get(msg_type) or {}
            content: dict[str, Any] = {"media_id": media.get("id"), "mime_type": media.get("mime_type")}
            if media.get("caption"):
                content["caption"] = media["caption"]
            return content
        # Tipos aún no soportados (location, interactive, reaction...): se conserva el bloque original.
        return {"raw": message.get(msg_type) or {}}
