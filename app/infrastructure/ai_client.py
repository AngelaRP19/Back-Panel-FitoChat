"""Cliente de IA del chatbot (guía §4): una interfaz y su versión simulada. Las versiones reales llegan en el módulo 11.

**La IA conduce las preguntas.** El cuestionario de síntomas no está escrito en la aplicación: en cada turno el
orquestador le entrega a la IA lo que se sabe del caso y lo último que dijo el productor, y la IA responde qué hacer:
si lo dicho es relevante y pertinente, cuál es la siguiente pregunta o si ya hay información suficiente. La aplicación
solo conecta y envía los mensajes, y filtra lo que la IA diga con el guardrail (nunca receta tratamientos).
"""

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, TypeVar

from app.core.config import get_settings

logger = logging.getLogger(__name__)
T = TypeVar("T")


class ErrorIA(RuntimeError):
    """La IA no respondió o respondió algo que no se puede interpretar."""


def con_reintentos(
    llamada: Callable[[], T], intentos: int, pausa: float, dormir: Callable[[float], None] = time.sleep
) -> T:
    """Llama a la IA hasta `intentos` veces (mínimo una), con pausa entre intentos; si todas fallan, lanza la última."""
    ultimo: ErrorIA | None = None
    for intento in range(max(intentos, 1)):
        try:
            return llamada()
        except ErrorIA as error:
            ultimo = error
            logger.warning("La IA falló", extra={"intento": intento + 1, "motivo": str(error)})
            if intento + 1 < max(intentos, 1):
                dormir(pausa)
    assert ultimo is not None
    raise ultimo


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


@dataclass(frozen=True)
class AnalisisIA:
    """Lo que la IA concluye de un caso: cuánto coinciden sus síntomas con cada categoría y por qué."""

    porcentajes: dict[str, float]  # código de categoría → porcentaje de coincidencia de síntomas (0 a 100)
    detalle: dict[str, dict[str, list[str]]] = field(
        default_factory=dict
    )  # código → {coinciden: [...], descartan: [...]}
    explicacion: str = ""


class AiClient(Protocol):
    def conducir(self, contexto: ContextoConsulta, mensaje: str | None) -> Turno:
        """`mensaje` responde a `contexto.pregunta_pendiente`; es None al iniciar el cuestionario."""
        ...

    def analizar(
        self, contexto: ContextoConsulta, imagenes: list[bytes], conocimiento: list[str], categorias: list[str]
    ) -> AnalisisIA:
        """Compara los síntomas (texto y fotos) con las categorías, apoyándose en fragmentos de conocimiento (RAG)."""
        ...

    def embedding(self, texto: str) -> list[float]:
        """Vector del texto para buscar en `conocimiento_embeddings`."""
        ...


@dataclass
class AiSimulado:
    """Sin IA real: devuelve los turnos que se le programen y, si no quedan, da por completo el cuestionario.

    Nunca inventa preguntas del dominio: en local simplemente salta a pedir las fotos.
    """

    turnos: deque[Turno] = field(default_factory=deque)
    llamadas: list[tuple[ContextoConsulta, str | None]] = field(default_factory=list)
    analisis: AnalisisIA | None = None  # lo que devolverá `analizar`; sin programar, no concluye nada
    dimension: int = 8

    def conducir(self, contexto: ContextoConsulta, mensaje: str | None) -> Turno:
        self.llamadas.append((contexto, mensaje))
        return self.turnos.popleft() if self.turnos else Turno(completo=True)

    def analizar(
        self, contexto: ContextoConsulta, imagenes: list[bytes], conocimiento: list[str], categorias: list[str]
    ) -> AnalisisIA:
        return self.analisis or AnalisisIA({}, {}, "")

    def embedding(self, texto: str) -> list[float]:
        return [0.0] * self.dimension


_SIMULADO = AiSimulado()


def get_ai_client() -> AiClient:
    if get_settings().integracion_ia == "simulada":
        return _SIMULADO
    from app.infrastructure.bedrock_client import get_bedrock_ai

    return get_bedrock_ai()
