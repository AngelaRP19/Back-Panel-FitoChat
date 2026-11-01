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


class EstadoMensaje(TypedDict):
    """Lo que Meta informa de un mensaje que enviamos: sent, delivered, read o failed (con su error)."""

    wamid: str
    estado: str
    destinatario: str
    codigo_error: int | None
    error: str | None


class WhatsAppPayloadAdapter:
    """Aísla el formato de WhatsApp Cloud API del resto del sistema."""

    @staticmethod
    def extract_messages(payload: dict[str, Any]) -> list[IncomingMessage]:
        """Recorre entry[].changes[].value.messages[]; los «statuses» se leen aparte (`extract_statuses`)."""
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
    def extract_statuses(payload: dict[str, Any]) -> list[EstadoMensaje]:
        """entry[].changes[].value.statuses[]: el destino de los mensajes que envió el bot o el panel."""
        estados: list[EstadoMensaje] = []
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                for status in (change.get("value") or {}).get("statuses") or []:
                    errores = status.get("errors") or [{}]
                    error = errores[0] if isinstance(errores[0], dict) else {}
                    datos_error = error.get("error_data") or {}
                    detalle = datos_error.get("details") or error.get("title") or error.get("message")
                    estados.append(
                        EstadoMensaje(
                            wamid=str(status.get("id", "")),
                            estado=str(status.get("status", "")),
                            destinatario=str(status.get("recipient_id", "")),
                            codigo_error=error.get("code"),
                            error=str(detalle) if detalle else None,
                        )
                    )
        return estados

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
        if msg_type == "interactive":  # botón o fila de lista pulsados: el id es la opción elegida
            interactivo = message.get("interactive") or {}
            tipo = str(interactivo.get("type", ""))
            respuesta = interactivo.get(tipo) or {}  # button_reply o list_reply
            return {"tipo": tipo, "id": str(respuesta.get("id", "")), "titulo": str(respuesta.get("title", ""))}
        if msg_type == "button":  # botón de respuesta rápida de una plantilla
            boton = message.get("button") or {}
            return {"tipo": "button", "id": str(boton.get("payload", "")), "titulo": str(boton.get("text", ""))}
        if msg_type == "location":
            lugar = message.get("location") or {}
            return {
                "latitud": lugar.get("latitude"),
                "longitud": lugar.get("longitude"),
                "nombre": lugar.get("name"),
                "direccion": lugar.get("address"),
            }
        # Tipos aún no soportados (reaction, contacts...): se conserva el bloque original.
        return {"raw": message.get(msg_type) or {}}
