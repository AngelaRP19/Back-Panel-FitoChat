"""Moderación y dominio de las fotos con Amazon Rekognition (guía §11, módulo 11).

Rekognition acepta JPEG y PNG de hasta 5 MB **como bytes**: por eso la foto se valida con el original, antes de
convertirla a WebP. Hace dos preguntas: ¿es contenido inapropiado? y ¿es una planta o un cultivo (dominio)?
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.config import get_settings
from app.infrastructure.ia_credenciales import ProveedorClientesIA, get_proveedor_ia

logger = logging.getLogger(__name__)

MAXIMO_BYTES = 5 * 1024 * 1024
# Etiquetas de Rekognition que indican que la foto es de una planta o de su fruto.
# Nota: "food" se excluye intencionalmente porque acepta alimentos servidos, platos,
# cuencos y artesanías sin presencia vegetal. Los frutos y cultivos reales siempre traen
# "fruit", "produce", "plant" o su jerarquía botánica en Parents.
ETIQUETAS_DE_PLANTA = frozenset(
    {
        "plant",
        "leaf",
        "fruit",
        "flower",
        "vegetation",
        "tree",
        "produce",
        "herbal",
        "vine",
        "herbs",
        "blossom",
        "bud",
        "sprout",
        "petal",
        "flora",
        "crop",
        "seedling",
    }
)

# Contextos no agrícolas que descalifican la foto para diagnóstico fitosanitario
# (arreglos florales comerciales, centros de mesa, presentación de platos):
ETIQUETAS_DE_EXCLUSION = frozenset(
    {
        "flower arrangement",
        "flower bouquet",
        "food presentation",
    }
)

# Etiquetas de paisaje amplio/panorámico que requieren evidencia cercana del cultivo:
ETIQUETAS_DE_PAISAJE = frozenset(
    {
        "scenery",
        "landscape",
    }
)

# Evidencia de partes del cultivo en primer plano para no descartar fotos que incluyan paisaje de fondo:
ETIQUETAS_DETALLE_CULTIVO = frozenset(
    {
        "leaf",
        "fruit",
        "produce",
        "herbal",
        "vine",
    }
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
        labels_list = etiquetas.get("Labels", [])
        etiquetas_dict = {str(e.get("Name", "")).lower(): float(e.get("Confidence", 0.0)) for e in labels_list}
        nombres = set(etiquetas_dict.keys())
        padres = {str(p.get("Name", "")).lower() for e in labels_list for p in e.get("Parents", [])}
        todas = nombres | padres

        # 1. Presencia botánica válida en nombres directos o jerarquía de padres (ej. familias o especies botánicas)
        tiene_botanica = bool(todas & ETIQUETAS_DE_PLANTA)

        # 2. Descalificación por arreglos ornamentales o presentaciones gastronómicas
        es_adorno = bool(nombres & ETIQUETAS_DE_EXCLUSION)

        # 3. Descalificación por paisajes panorámicos (bosques, montañas, horizontes) sin primer plano de cultivo
        es_paisaje = any(etiquetas_dict.get(p, 0.0) >= self._confianza for p in ETIQUETAS_DE_PAISAJE)
        tiene_detalle_cultivo = bool(nombres & ETIQUETAS_DETALLE_CULTIVO)
        es_panorama_sin_cultivo = es_paisaje and not tiene_detalle_cultivo

        es_planta = tiene_botanica and not es_adorno and not es_panorama_sin_cultivo
        if not es_planta:  # las etiquetas permiten ajustar las listas con fotos reales rechazadas
            logger.info(
                "Rekognition no reconoce un cultivo en la foto",
                extra={
                    "etiquetas": sorted(nombres)[:20],
                    "tiene_botanica": tiene_botanica,
                    "es_adorno": es_adorno,
                    "es_panorama_sin_cultivo": es_panorama_sin_cultivo,
                },
            )

        return ResultadoModeracion(
            inapropiada=bool(moderacion.get("ModerationLabels")),
            es_planta=es_planta,
        )


def get_moderador(proveedor: ProveedorClientesIA | None = None) -> Moderador:
    ajustes = get_settings()
    if ajustes.integracion_ia == "simulada":
        return ModeradorSimulado()
    origen = proveedor or get_proveedor_ia()
    return RekognitionReal(lambda: origen.cliente("rekognition"), ajustes.foto_confianza_minima)
