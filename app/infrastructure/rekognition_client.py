"""Moderación y dominio de las fotos con Amazon Rekognition (guía §11, módulo 11).

Rekognition acepta JPEG y PNG de hasta 5 MB **como bytes**: por eso la foto se valida con el original, antes de
convertirla a WebP. Hace dos preguntas: ¿es contenido inapropiado? y ¿es una planta o un cultivo (dominio)?
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.config import get_settings
from app.infrastructure.ia_credenciales import ProveedorClientesIA, get_proveedor_ia

MAXIMO_BYTES = 5 * 1024 * 1024
# Etiquetas de Rekognition que indican que la foto es de una planta o de su fruto.
ETIQUETAS_DE_PLANTA = frozenset(
    {"plant", "leaf", "fruit", "flower", "vegetation", "tree", "produce", "food", "herbal", "vine", "herbs", "blossom"}
)


class ErrorModeracion(RuntimeError):
    """Rekognition no respondió. La foto no se descarta por eso: el consumidor reintenta el mensaje."""


@dataclass(frozen=True)
class ResultadoModeracion:
    inapropiada: bool
    es_planta: bool


class Moderador(Protocol):
    def analizar(self, imagen: bytes) -> ResultadoModeracion: ...


@dataclass
class ModeradorSimulado:
    """Acepta toda foto como planta; las pruebas pueden programar rechazos por contenido de los bytes."""

    inapropiadas: set[bytes] = field(default_factory=set)
    no_plantas: set[bytes] = field(default_factory=set)
    analizadas: int = 0

    def analizar(self, imagen: bytes) -> ResultadoModeracion:
        self.analizadas += 1
        return ResultadoModeracion(inapropiada=imagen in self.inapropiadas, es_planta=imagen not in self.no_plantas)


class RekognitionReal:
    def __init__(self, cliente: Any, confianza_minima: float) -> None:
        self._cliente = cliente  # callable que devuelve el cliente vigente (las credenciales se renuevan)
        self._confianza = confianza_minima

    def analizar(self, imagen: bytes) -> ResultadoModeracion:
        try:
            rk = self._cliente()
            moderacion = rk.detect_moderation_labels(Image={"Bytes": imagen}, MinConfidence=self._confianza)
            etiquetas = rk.detect_labels(Image={"Bytes": imagen}, MinConfidence=self._confianza, MaxLabels=25)
        except Exception as error:
            raise ErrorModeracion(f"Falló Rekognition: {type(error).__name__}") from error
        nombres = {str(e["Name"]).lower() for e in etiquetas.get("Labels", [])}
        return ResultadoModeracion(
            inapropiada=bool(moderacion.get("ModerationLabels")), es_planta=bool(nombres & ETIQUETAS_DE_PLANTA)
        )


def get_moderador(proveedor: ProveedorClientesIA | None = None) -> Moderador:
    ajustes = get_settings()
    if ajustes.integracion_ia == "simulada":
        return ModeradorSimulado()
    origen = proveedor or get_proveedor_ia()
    return RekognitionReal(lambda: origen.cliente("rekognition"), ajustes.foto_confianza_minima)
