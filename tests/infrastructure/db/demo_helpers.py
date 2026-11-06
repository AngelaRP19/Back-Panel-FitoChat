"""Constantes y consultas compartidas por las pruebas de los datos demo."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.infrastructure.db.seed_demo import CASOS, CASOS_TRIPS_CENTRO, CASOS_TRIPS_SAN_LUIS

AHORA = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
BUCKET = "anuc-evidencias-demo"
TODOS = [*CASOS, *CASOS_TRIPS_CENTRO, *CASOS_TRIPS_SAN_LUIS]
TABLAS_DEMO = (
    "productores",
    "fincas",
    "cultivos",
    "cultivo_especie",
    "casos",
    "eventos",
    "asignaciones",
    "diagnosticos",
    "respuestas",
    "fotografias",
    "preguntas",
    "identificacion_preliminar",
    "coincidencias_categoria",
    "alertas",
    "alerta_casos",
    "alerta_destinatarios",
    "tecnicos",
    "lideres_tecnicos",
)


def contar(session: Session) -> dict[str, int]:
    return {t: int(session.scalar(text(f"SELECT count(*) FROM {t}")) or 0) for t in TABLAS_DEMO}


def escalar(session: Session, sql: str, **parametros: Any) -> Any:
    return session.scalar(text(sql), parametros)


def filas(session: Session, sql: str, **parametros: Any) -> list[dict[str, Any]]:
    return [dict(f) for f in session.execute(text(sql), parametros).mappings()]


def versiones(crudo: Any) -> list[dict[str, Any]]:
    r = crudo.list_object_versions(Bucket=BUCKET)
    return [*r.get("Versions", []), *r.get("DeleteMarkers", [])]
