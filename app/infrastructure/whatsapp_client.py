"""Cliente de WhatsApp (guía §4): una interfaz y su versión simulada. El cliente real llega en la Fase C.

En modo simulado (`INTEGRACION_WHATSAPP=simulada`) nunca sale un mensaje real: el resultado de cada envío es
`SIMULADO`, no `ENVIADO`, para que nadie confunda una demostración con un aviso entregado.
"""

from dataclasses import dataclass, field
from itertools import count
from typing import Protocol

from app.core.config import get_settings

ENVIADO = "ENVIADO"
SIMULADO = "SIMULADO"
FALLIDO = "FALLIDO"


@dataclass(frozen=True)
class ResultadoEnvio:
    estado: str  # ENVIADO, SIMULADO o FALLIDO
    id_externo: str | None = None
    error: str | None = None

    @property
    def exito(self) -> bool:
        return self.estado != FALLIDO


class WhatsAppClient(Protocol):
    def enviar_texto(self, telefono_e164: str, texto: str) -> ResultadoEnvio:
        """Envía un mensaje de texto. No lanza excepciones por un número rechazado: lo informa en el resultado."""
        ...


@dataclass
class WhatsAppSimulado:
    """No envía nada: anota los mensajes (para las pruebas) y puede simular números que fallan."""

    enviados: list[tuple[str, str]] = field(default_factory=list)
    fallan: set[str] = field(default_factory=set)
    _consecutivo: "count[int]" = field(default_factory=lambda: count(1))

    def enviar_texto(self, telefono_e164: str, texto: str) -> ResultadoEnvio:
        if telefono_e164 in self.fallan:
            return ResultadoEnvio(FALLIDO, error="Simulado: el número rechazó el mensaje")
        self.enviados.append((telefono_e164, texto))
        return ResultadoEnvio(SIMULADO, id_externo=f"sim-{next(self._consecutivo)}")

    def reiniciar(self) -> None:
        self.enviados.clear()
        self.fallan.clear()


_SIMULADO = WhatsAppSimulado()


def get_whatsapp_client() -> WhatsAppClient:
    if get_settings().integracion_whatsapp == "simulada":
        return _SIMULADO
    raise RuntimeError("El cliente real de WhatsApp llega en la Fase C")
