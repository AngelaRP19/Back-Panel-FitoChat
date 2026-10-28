"""Mapa de vigilancia (pantalla 19 · B-09 · solo líder): GET /mapa/casos?especie&desde&hasta.

Casos con resultado técnico Confirmado u Otra patología, con la ubicación de su finca y un nivel de concentración 1-4
calculado con la **misma regla de las alertas** (`sp_evaluar_alerta`): casos de la misma categoría confirmada a
`distancia_km` o menos y con diagnósticos separados por `intervalo_dias` o menos, según los parámetros vigentes. Una
categoría que no participa en las alertas (no transmisible o sin parámetros) tiene `concentracion = null`.

Cortes (propuesta de la guía, B-09): 1 = el caso solo · 2 = dos casos · 3 = de tres casos hasta uno menos que el
umbral · 4 = umbral o más.
"""

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError, ErrorRespuesta
from app.core.catalogos_api import CATEGORIAS, ESPECIES, CategoriaFito, EspecieCultivo, normalizar_codigo
from app.core.codigos import codigo_caso
from app.core.roles import Rol
from app.core.security_cognito import UsuarioActual, requiere_rol
from app.infrastructure.db.session import get_session

router = APIRouter(prefix="/mapa", tags=["mapa"])
SoloLider = Annotated[UsuarioActual, Depends(requiere_rol(Rol.LIDER_TECNICO))]
Sesion = Annotated[AsyncSession, Depends(get_session)]
_RESPUESTAS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorRespuesta},
    403: {"model": ErrorRespuesta, "description": "Solo el líder técnico."},
    422: {"model": ErrorRespuesta, "description": "VALIDACION: fechas inválidas."},
}


class CasoMapa(BaseModel):
    id: str
    codigo: str
    categoria: str
    resultado_tecnico: str
    especie: str
    productor_nombre: str
    finca_id: str
    finca_nombre: str
    vereda: str = Field(description="Código de la vereda")
    fecha: datetime = Field(description="Envío al panel")
    latitud: float
    longitud: float
    concentracion: int | None = Field(description="1-4 si la categoría participa en las alertas; si no, nulo")


class DatosMapa(BaseModel):
    casos: list[CasoMapa]
    conteos: dict[str, int] = Field(description="Casos por categoría (todas las categorías, aunque sea 0)")


def nivel(vecinos: int, umbral: int) -> int:
    """`vecinos` incluye el caso mismo."""
    if vecinos >= umbral:
        return 4
    if vecinos >= 3:  # de 3 casos hasta uno menos que el umbral
        return 3
    return 2 if vecinos == 2 else 1


_SQL = """
WITH base AS (
    SELECT d.id_caso, d.id_categoria_confirmada AS id_cat, cf.nombre_categoria,
           cf.es_transmisible = 'S' AS transmisible,
           d.resultado_tecnico, d.fecha_diagnostico, c.fecha_envio_panel, e.nombre_especie,
           p.nombre_productor, f.id_finca, f.nombre_finca, f.vereda_sector_finca, f.latitud_finca, f.longitud_finca,
           f.ubicacion
    FROM diagnosticos d
    JOIN categorias_fitosanitarias cf ON cf.id_categoria = d.id_categoria_confirmada
    JOIN casos c ON c.id_caso = d.id_caso
    JOIN especies e ON e.id_especie = c.id_especie
    JOIN cultivos cu ON cu.id_cultivo = c.id_cultivo
    JOIN fincas f ON f.id_finca = cu.id_finca
    JOIN productores p ON p.id_productor = f.id_productor
    WHERE d.resultado_tecnico IN ('CONFIRMADO', 'OTRA_PATOLOGIA') AND d.fecha_diagnostico IS NOT NULL
      AND f.latitud_finca IS NOT NULL AND f.longitud_finca IS NOT NULL
)
SELECT b.*, pe.numero_minimo_casos AS umbral,
       CASE WHEN b.transmisible AND pe.id_categoria IS NOT NULL THEN (
           SELECT count(*) FROM base g
           WHERE g.id_cat = b.id_cat
             AND abs(EXTRACT(EPOCH FROM (g.fecha_diagnostico - b.fecha_diagnostico))) <= pe.intervalo_dias * 86400
             AND fn_distancia_km(b.ubicacion, g.ubicacion) <= pe.distancia_km
       ) END AS vecinos
FROM base b
LEFT JOIN parametros_epidemiologicos pe ON pe.id_categoria = b.id_cat
WHERE (CAST(:especie AS varchar) IS NULL OR b.nombre_especie = :especie)
  AND (CAST(:desde AS date) IS NULL OR (b.fecha_envio_panel AT TIME ZONE 'America/Bogota')::date >= :desde)
  AND (CAST(:hasta AS date) IS NULL OR (b.fecha_envio_panel AT TIME ZONE 'America/Bogota')::date <= :hasta)
ORDER BY b.fecha_envio_panel DESC NULLS LAST, b.id_caso
"""


@router.get("/casos", response_model=DatosMapa, responses=_RESPUESTAS)
async def casos_del_mapa(
    _: SoloLider,
    session: Sesion,
    especie: EspecieCultivo | None = None,
    desde: Annotated[date | None, Query(description="Envío al panel desde (aaaa-mm-dd, hora de Colombia)")] = None,
    hasta: Annotated[date | None, Query(description="Envío al panel hasta (aaaa-mm-dd)")] = None,
) -> DatosMapa:
    if desde and hasta and desde > hasta:
        raise ApiError(422, "VALIDACION", "Revisa los campos marcados.", {"desde": "Debe ser anterior a «hasta»."})
    filas = (
        await session.execute(
            text(_SQL),
            {"especie": ESPECIES.a_nombre(especie) if especie else None, "desde": desde, "hasta": hasta},
        )
    ).mappings()
    casos = [
        CasoMapa(
            id=f["id_caso"],
            codigo=codigo_caso(f["id_caso"]),
            categoria=CATEGORIAS.a_codigo(f["nombre_categoria"]),
            resultado_tecnico=f["resultado_tecnico"],
            especie=ESPECIES.a_codigo(f["nombre_especie"]),
            productor_nombre=f["nombre_productor"],
            finca_id=f["id_finca"],
            finca_nombre=f["nombre_finca"],
            vereda=normalizar_codigo(f["vereda_sector_finca"]),
            fecha=f["fecha_envio_panel"] or f["fecha_diagnostico"],
            latitud=float(f["latitud_finca"]),
            longitud=float(f["longitud_finca"]),
            concentracion=nivel(int(f["vecinos"]), int(f["umbral"])) if f["vecinos"] is not None else None,
        )
        for f in filas
    ]
    conteos = {c.value: 0 for c in CategoriaFito}
    for caso in casos:
        conteos[caso.categoria] = conteos.get(caso.categoria, 0) + 1
    return DatosMapa(casos=casos, conteos=conteos)
