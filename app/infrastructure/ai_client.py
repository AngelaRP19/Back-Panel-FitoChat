"""Cliente de IA del chatbot (guía §4): una interfaz y su versión simulada. Las versiones reales llegan en el módulo 11.

**La IA conduce las preguntas.** El cuestionario de síntomas no está escrito en la aplicación: en cada turno el
orquestador le entrega a la IA lo que se sabe del caso y lo último que dijo el productor, y la IA responde qué hacer:
si lo dicho es relevante y pertinente, cuál es la siguiente pregunta o si ya hay información suficiente. La aplicación
solo conecta y envía los mensajes, y filtra lo que la IA diga con el guardrail (nunca receta tratamientos).
"""

from collections import deque
from dataclasses import dataclass, field
from typing import Protocol

from app.core.config import get_settings


@dataclass(frozen=True)
class ContextoConsulta:
    """Lo que la IA necesita para conducir la consulta de un caso."""

    especie: str  # código de la API (GULUPA…)
    cultivo: str
    zona_afectada: str
    historial: tuple[tuple[str, str], ...]  # (pregunta, respuesta) relevantes hasta ahora
    pregunta_pendiente: str | None  # la última pregunta que se le hizo al productor y aún no se evalúa


@dataclass(frozen=True)
class Turno:
    """Decisión de la IA para este turno."""

    pregunta: str | None = None  # siguiente pregunta; nula si ya no necesita más
    completo: bool = False  # hay información suficiente: se pasa a pedir las fotos
    relevante: bool = True  # lo que el productor acaba de responder sirve para el diagnóstico (si no, no se guarda)
    fuera_de_dominio: bool = False  # lo dicho no tiene que ver con el cultivo ni con sus problemas fitosanitarios


class AiClient(Protocol):
    def conducir(self, contexto: ContextoConsulta, mensaje: str | None) -> Turno:
        """`mensaje` responde a `contexto.pregunta_pendiente`; es None al iniciar el cuestionario."""
        ...


@dataclass
class AiSimulado:
    """Sin IA real: devuelve los turnos que se le programen y, si no quedan, da por completo el cuestionario.

    Nunca inventa preguntas del dominio: en local simplemente salta a pedir las fotos.
    """

    turnos: deque[Turno] = field(default_factory=deque)
    llamadas: list[tuple[ContextoConsulta, str | None]] = field(default_factory=list)

    def conducir(self, contexto: ContextoConsulta, mensaje: str | None) -> Turno:
        self.llamadas.append((contexto, mensaje))
        return self.turnos.popleft() if self.turnos else Turno(completo=True)


_SIMULADO = AiSimulado()


def get_ai_client() -> AiClient:
    if get_settings().integracion_ia == "simulada":
        return _SIMULADO
    raise RuntimeError("El cliente real de IA llega en el módulo 11")
