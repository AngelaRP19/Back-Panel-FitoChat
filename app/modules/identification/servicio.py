"""Identificación preliminar del caso (guía §2 y §11, módulo 11): RAG + IA + reglas del 50 % y los 15 puntos.

Pasos al enviar el caso al panel:
1. Se arma una consulta con la especie, la zona afectada y las respuestas, y se pide su embedding.
2. Se buscan en `conocimiento_embeddings` (pgvector, distancia coseno) los fragmentos técnicos más cercanos: eso es el
   RAG, lo que ancla a la IA en el conocimiento de la ANUC en lugar de su memoria.
3. La IA recibe síntomas, fotos y fragmentos, y estima el **porcentaje de coincidencia de síntomas** con cada categoría.
4. Las reglas (`reglas.py`) deciden si hay una categoría identificada, si hay ambigüedad o si no se identificó nada.
5. Todo texto de la IA pasa por el guardrail: si recomienda tratamientos o habla de «confianza» o «probabilidad», se
   descarta.

Si la IA no responde tras los reintentos, devuelve `None`: el caso llega al panel sin identificación preliminar (D-03)
y el técnico decide por su cuenta. Nunca se inventa un resultado.
"""

import logging

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.catalogos_api import CATEGORIAS
from app.core.config import Settings, get_settings
from app.core.guardrail import es_seguro
from app.infrastructure.ai_client import AiClient, ContextoConsulta, ErrorIA, con_reintentos
from app.infrastructure.s3_storage_client import S3StorageClient
from app.modules.identification.reglas import resultado_de
from app.modules.orchestrator import repositorio as repo
from app.modules.orchestrator.puertos import Identificacion

logger = logging.getLogger(__name__)

FRAGMENTOS_RAG = 5
EXPLICACION_NEUTRA = (
    "Identificación preliminar por porcentaje de coincidencia de síntomas. El diagnóstico lo hace el técnico."
)


def literal_vector(vector: list[float]) -> str:
    """Formato de texto de pgvector: «[0.1,0.2,…]»."""
    return "[" + ",".join(f"{x:.8g}" for x in vector) + "]"


def fragmentos_cercanos(s: Session, vector: list[float], cuantos: int = FRAGMENTOS_RAG) -> list[str]:
    filas = s.scalars(
        text("SELECT texto FROM conocimiento_embeddings ORDER BY embedding <=> CAST(:v AS vector) LIMIT :k"),
        {"v": literal_vector(vector), "k": cuantos},
    )
    return [str(x) for x in filas]


def consulta_de(contexto: ContextoConsulta) -> str:
    respuestas = " ".join(r for _, r in contexto.historial)
    return f"{contexto.especie} {contexto.zona_afectada} {respuestas}".strip()


def depurar_detalle(detalle: dict[str, dict[str, list[str]]]) -> dict[str, dict[str, list[str]]]:
    """Quita del detalle toda frase que no pase el guardrail."""
    return {
        codigo: {clave: [f for f in frases if es_seguro(f)] for clave, frases in partes.items()}
        for codigo, partes in detalle.items()
    }


class IdentificadorIA:
    def __init__(
        self, ai: AiClient, s3: S3StorageClient, ajustes: Settings | None = None, fragmentos: int = FRAGMENTOS_RAG
    ) -> None:
        self.ai = ai
        self.s3 = s3
        self.ajustes = ajustes or get_settings()
        self.fragmentos = fragmentos

    def _imagenes(self, s: Session, id_caso: str) -> list[bytes]:
        imagenes: list[bytes] = []
        for clave in repo.rutas_de_fotos(s, id_caso):
            try:
                imagenes.append(self.s3.descargar(clave))
            except (ClientError, BotoCoreError):
                logger.warning("No se pudo leer una foto del caso", extra={"id_caso": id_caso, "clave": clave})
        return imagenes

    def identificar(self, s: Session, id_caso: str) -> Identificacion | None:
        caso = repo.caso_abierto(s, id_caso)
        if caso is None:
            return None
        contexto = ContextoConsulta(caso.especie, caso.nombre_cultivo, caso.zona, repo.historial(s, id_caso), None)
        imagenes = self._imagenes(s, id_caso)
        try:
            vector = con_reintentos(
                lambda: self.ai.embedding(consulta_de(contexto)),
                self.ajustes.ia_reintentos,
                self.ajustes.ia_pausa_reintento_segundos,
            )
            conocimiento = fragmentos_cercanos(s, vector, self.fragmentos)
            analisis = con_reintentos(
                lambda: self.ai.analizar(contexto, imagenes, conocimiento, CATEGORIAS.codigos),
                self.ajustes.ia_reintentos,
                self.ajustes.ia_pausa_reintento_segundos,
            )
        except ErrorIA:
            logger.error(
                "No se pudo identificar el caso; llega al panel sin identificación", extra={"id_caso": id_caso}
            )
            return None

        porcentajes = {c: round(p, 2) for c, p in analisis.porcentajes.items() if c in CATEGORIAS.codigos}
        tipo = resultado_de(porcentajes, self.ajustes.umbral_coincidencia, self.ajustes.umbral_ambiguo)
        explicacion = analisis.explicacion if analisis.explicacion and es_seguro(analisis.explicacion) else None
        if explicacion is None and analisis.explicacion:
            logger.warning("Se descartó la explicación de la IA por el guardrail", extra={"id_caso": id_caso})
        return Identificacion(
            tipo_resultado=tipo.value,
            explicacion=explicacion or EXPLICACION_NEUTRA,
            porcentajes=porcentajes,
            detalle=depurar_detalle(analisis.detalle) or None,
        )
