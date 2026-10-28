"""Cliente de WhatsApp (guía §4): una interfaz, su versión simulada y el cliente real de la Cloud API de Meta.

En modo simulado (`INTEGRACION_WHATSAPP=simulada`) nunca sale un mensaje real: el resultado de cada envío es
`SIMULADO`, no `ENVIADO`, para que nadie confunda una demostración con un aviso entregado.

El cliente real (`WhatsAppCloud`) envía texto y mensajes interactivos (botones, listas, pedido de ubicación y
ubicación; ver `whatsapp_mensajes`) y descarga las fotos que mandan los productores (Graph API: primero se
pide la URL del medio y luego se descarga con el mismo token). Un número que Meta rechaza no es una excepción: se
informa en el resultado; una falla de red o un 5xx tampoco rompe el flujo, pero sí queda registrada como `FALLIDO`.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import count
from typing import Any, Protocol

import httpx

from app.core.config import get_settings
from app.infrastructure.whatsapp_mensajes import Interactivo, texto_visible

ENVIADO = "ENVIADO"
SIMULADO = "SIMULADO"
FALLIDO = "FALLIDO"

GRAPH_URL = "https://graph.facebook.com"
TIMEOUT_SEGUNDOS = 15.0

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResultadoEnvio:
    estado: str  # ENVIADO, SIMULADO o FALLIDO
    id_externo: str | None = None
    error: str | None = None

    @property
    def exito(self) -> bool:
        return self.estado != FALLIDO


class ErrorDescargaMedia(RuntimeError):
    """No se pudo bajar la foto de Meta (no existe, venció o Meta no respondió)."""


class WhatsAppClient(Protocol):
    def enviar_texto(self, telefono_e164: str, texto: str) -> ResultadoEnvio:
        """Envía un mensaje de texto. No lanza excepciones por un número rechazado: lo informa en el resultado."""
        ...

    def enviar_interactivo(self, telefono_e164: str, mensaje: Interactivo) -> ResultadoEnvio:
        """Envía botones, una lista, un pedido de ubicación o una ubicación. Igual que `enviar_texto`: no lanza."""
        ...

    def descargar_media(self, media_id: str) -> bytes:
        """Descarga una foto recibida. Lanza `ErrorDescargaMedia` si no se pudo."""
        ...


@dataclass
class WhatsAppSimulado:
    """No envía nada: anota los mensajes (para las pruebas) y puede simular números que fallan."""

    enviados: list[tuple[str, str]] = field(default_factory=list)  # teléfono y texto visible de cada mensaje
    interactivos: list[tuple[str, Interactivo]] = field(default_factory=list)  # los interactivos, completos
    fallan: set[str] = field(default_factory=set)
    medios: dict[str, bytes] = field(default_factory=dict)  # media_id → bytes de la foto
    _consecutivo: "count[int]" = field(default_factory=lambda: count(1))

    def enviar_texto(self, telefono_e164: str, texto: str) -> ResultadoEnvio:
        if telefono_e164 in self.fallan:
            return ResultadoEnvio(FALLIDO, error="Simulado: el número rechazó el mensaje")
        self.enviados.append((telefono_e164, texto))
        return ResultadoEnvio(SIMULADO, id_externo=f"sim-{next(self._consecutivo)}")

    def enviar_interactivo(self, telefono_e164: str, mensaje: Interactivo) -> ResultadoEnvio:
        if telefono_e164 in self.fallan:
            return ResultadoEnvio(FALLIDO, error="Simulado: el número rechazó el mensaje")
        self.enviados.append((telefono_e164, texto_visible(mensaje)))
        self.interactivos.append((telefono_e164, mensaje))
        return ResultadoEnvio(SIMULADO, id_externo=f"sim-{next(self._consecutivo)}")

    def descargar_media(self, media_id: str) -> bytes:
        if media_id not in self.medios:
            raise ErrorDescargaMedia(f"Simulado: no hay foto {media_id}")
        return self.medios[media_id]

    def reiniciar(self) -> None:
        self.enviados.clear()
        self.interactivos.clear()
        self.fallan.clear()
        self.medios.clear()


class WhatsAppCloud:
    """Cliente real de la Cloud API. `transporte` permite probarlo sin salir a internet."""

    def __init__(
        self,
        token: str,
        phone_number_id: str,
        version: str = "v21.0",
        transporte: httpx.BaseTransport | None = None,
        tiempo_maximo: float = TIMEOUT_SEGUNDOS,
    ) -> None:
        self._base = f"{GRAPH_URL}/{version}"
        self._phone_number_id = phone_number_id
        self._http = httpx.Client(
            headers={"Authorization": f"Bearer {token}"}, timeout=tiempo_maximo, transport=transporte
        )

    def enviar_texto(self, telefono_e164: str, texto: str) -> ResultadoEnvio:
        return self._enviar(telefono_e164, {"type": "text", "text": {"body": texto, "preview_url": False}})

    def enviar_interactivo(self, telefono_e164: str, mensaje: Interactivo) -> ResultadoEnvio:
        return self._enviar(telefono_e164, mensaje.cuerpo())

    def _enviar(self, telefono_e164: str, contenido: dict[str, Any]) -> ResultadoEnvio:
        cuerpo: dict[str, Any] = {"messaging_product": "whatsapp", "to": telefono_e164, **contenido}
        try:
            respuesta = self._http.post(f"{self._base}/{self._phone_number_id}/messages", json=cuerpo)
        except httpx.HTTPError as error:
            return ResultadoEnvio(FALLIDO, error=f"Sin respuesta de Meta: {type(error).__name__}")
        if respuesta.status_code >= 400:
            # Solo el código de error de Meta: el cuerpo puede traer el número del productor.
            return ResultadoEnvio(FALLIDO, error=f"Meta respondió {respuesta.status_code}{_codigo_error(respuesta)}")
        mensajes = respuesta.json().get("messages") or [{}]
        return ResultadoEnvio(ENVIADO, id_externo=mensajes[0].get("id"))

    def descargar_media(self, media_id: str) -> bytes:
        try:
            meta = self._http.get(f"{self._base}/{media_id}")
            if meta.status_code >= 400:
                raise ErrorDescargaMedia(f"Meta respondió {meta.status_code} al pedir la foto")
            url = meta.json().get("url")
            if not isinstance(url, str) or not url:
                raise ErrorDescargaMedia("Meta no devolvió la URL de la foto")
            contenido = self._http.get(url)  # la URL del medio también pide el token
        except httpx.HTTPError as error:
            raise ErrorDescargaMedia(f"Sin respuesta de Meta: {type(error).__name__}") from error
        if contenido.status_code >= 400:
            raise ErrorDescargaMedia(f"Meta respondió {contenido.status_code} al descargar la foto")
        return contenido.content

    def cerrar(self) -> None:
        self._http.close()


def _codigo_error(respuesta: httpx.Response) -> str:
    try:
        codigo = respuesta.json().get("error", {}).get("code")
    except ValueError:
        return ""
    return f" (código {codigo})" if codigo is not None else ""


_SIMULADO = WhatsAppSimulado()
_REAL: WhatsAppCloud | None = None


def get_whatsapp_client(fabrica: Callable[..., WhatsAppCloud] = WhatsAppCloud) -> WhatsAppClient:
    global _REAL
    ajustes = get_settings()
    if ajustes.integracion_whatsapp == "simulada":
        return _SIMULADO
    if _REAL is None:
        if ajustes.whatsapp_access_token is None or not ajustes.whatsapp_phone_number_id:
            raise RuntimeError("Faltan WHATSAPP_ACCESS_TOKEN y WHATSAPP_PHONE_NUMBER_ID")
        _REAL = fabrica(
            ajustes.whatsapp_access_token.get_secret_value(),
            ajustes.whatsapp_phone_number_id,
            ajustes.whatsapp_graph_version,
        )
    return _REAL
