"""Casos del panel (guía §10.4): bandeja, conteos, detalle, partes del caso y asignación.

El líder ve todos los casos; el técnico, solo los asignados. Lo ajeno responde `404`, igual que lo inexistente.
El diagnóstico y el cierre (módulo 5) son los POST al final.
"""

import logging
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ErrorRespuesta
from app.api.paginacion import Pagina, ParametrosPagina, parametros_pagina
from app.core.catalogos_api import CategoriaFito, EspecieCultivo, EstadoCaso
from app.core.roles import Rol
from app.core.security_cognito import UsuarioActual, requiere_rol
from app.infrastructure.db.session import get_session
from app.infrastructure.s3_storage_client import S3StorageClient, get_s3_storage_client
from app.modules.panel_api.casos.repository import NO_IDENTIFICADA, FiltrosCasos
from app.modules.panel_api.casos.schemas import (
    AsignarIn,
    CasoDetalle,
    CasoResumen,
    ConteosCasos,
    CuestionarioOut,
    DiagnosticoIn,
    EventoOut,
    EvidenciaOut,
    ExplicacionOut,
)
from app.modules.panel_api.casos.service import CasosService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/casos", tags=["casos"])

RESPUESTAS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorRespuesta, "description": "Sin token, vencido o inválido."},
    403: {"model": ErrorRespuesta, "description": "Sin permiso para esta acción."},
    404: {"model": ErrorRespuesta, "description": "El caso no existe o no está entre los asignados al técnico."},
    409: {"model": ErrorRespuesta, "description": "TRANSICION_INVALIDA o CASO_YA_ASIGNADO."},
    422: {"model": ErrorRespuesta, "description": "VALIDACION: datos o filtros inválidos."},
}

Usuario = Annotated[UsuarioActual, Depends(requiere_rol(Rol.LIDER_TECNICO, Rol.TECNICO))]
SoloLider = Annotated[UsuarioActual, Depends(requiere_rol(Rol.LIDER_TECNICO))]
SoloTecnico = Annotated[UsuarioActual, Depends(requiere_rol(Rol.TECNICO))]


def get_s3_opcional() -> S3StorageClient | None:
    """Sin bucket configurado (por ejemplo, en local) las evidencias se listan sin URL."""
    try:
        return get_s3_storage_client()
    except RuntimeError:
        logger.warning("S3_BUCKET_EVIDENCIAS no está configurado: las evidencias salen sin URL")
        return None


def get_servicio(
    session: Annotated[AsyncSession, Depends(get_session)],
    s3: Annotated[S3StorageClient | None, Depends(get_s3_opcional)],
) -> CasosService:
    return CasosService(session, s3)


Servicio = Annotated[CasosService, Depends(get_servicio)]


def filtros_casos(
    q: Annotated[str | None, Query(max_length=100, description="Productor, finca o número de caso (#0241)")] = None,
    vereda: Annotated[str | None, Query(max_length=100, description="Código, por ejemplo SAN_LUIS")] = None,
    finca_id: str | None = None,
    cultivo_id: str | None = None,
    especie: EspecieCultivo | None = None,
    categoria: Annotated[
        CategoriaFito | Literal["NO_IDENTIFICADA"] | None, Query(description="Categoría propuesta o NO_IDENTIFICADA")
    ] = None,
    tecnico_id: Annotated[str | None, Query(description="Id de la cuenta o `sin_asignar`")] = None,
    coincidencia_min: Annotated[int | None, Query(ge=0, le=100, description="Coincidencia de síntomas mínima")] = None,
    vencimiento: Annotated[Literal["15", "30"] | None, Query(description="Vencen en los próximos N días")] = None,
    desde: Annotated[date | None, Query(description="Enviados al panel desde (aaaa-mm-dd, hora de Colombia)")] = None,
    hasta: Annotated[date | None, Query(description="Enviados al panel hasta (aaaa-mm-dd, hora de Colombia)")] = None,
) -> FiltrosCasos:
    return FiltrosCasos(
        q=q,
        vereda=vereda,
        finca_id=finca_id,
        cultivo_id=cultivo_id,
        especie=especie.value if especie else None,
        categoria=categoria.value if isinstance(categoria, CategoriaFito) else categoria,
        tecnico_id=tecnico_id,
        coincidencia_min=coincidencia_min,
        vencimiento=vencimiento,
        desde=desde,
        hasta=hasta,
    )


Filtros = Annotated[FiltrosCasos, Depends(filtros_casos)]

__all__ = ["NO_IDENTIFICADA", "router"]


@router.get("", response_model=Pagina[CasoResumen], responses=RESPUESTAS)
async def listar_casos(
    actor: Usuario,
    servicio: Servicio,
    filtros: Filtros,
    pagina: Annotated[ParametrosPagina, Depends(parametros_pagina)],
    estado: EstadoCaso | None = None,
) -> Pagina[CasoResumen]:
    """Bandeja: más recientes (por envío al panel) primero; los que aún no se envían, al final (P-03)."""
    items, total = await servicio.listar(actor, filtros, estado, pagina)
    return Pagina[CasoResumen](items=items, total=total, page=pagina.page, page_size=pagina.page_size)


@router.get("/conteos", response_model=ConteosCasos, responses=RESPUESTAS)
async def conteos_casos(actor: Usuario, servicio: Servicio, filtros: Filtros) -> ConteosCasos:
    """Casos por estado para las pestañas, con el mismo alcance y filtros del listado (sin el de estado)."""
    return await servicio.conteos(actor, filtros)


@router.get("/{id_caso}", response_model=CasoDetalle, responses=RESPUESTAS)
async def obtener_caso(id_caso: str, actor: Usuario, servicio: Servicio) -> CasoDetalle:
    """Consolidado del caso, con `puede_diagnosticar`, `puede_cerrar` y `puede_asignar` para quien consulta."""
    return await servicio.detalle(actor, id_caso)


@router.get("/{id_caso}/evidencias", response_model=list[EvidenciaOut], responses=RESPUESTAS)
async def evidencias_del_caso(id_caso: str, actor: Usuario, servicio: Servicio) -> list[EvidenciaOut]:
    """Fotos con URL prefirmada de corta vigencia (`url_expira_en`)."""
    return await servicio.evidencias(actor, id_caso)


@router.get("/{id_caso}/cuestionario", response_model=CuestionarioOut, responses=RESPUESTAS)
async def cuestionario_del_caso(id_caso: str, actor: Usuario, servicio: Servicio) -> CuestionarioOut:
    return await servicio.cuestionario(actor, id_caso)


@router.get("/{id_caso}/explicacion", response_model=ExplicacionOut, responses=RESPUESTAS)
async def explicacion_del_caso(id_caso: str, actor: Usuario, servicio: Servicio) -> ExplicacionOut:
    """Por qué se propone esa identificación preliminar. `404 SIN_ANALISIS` si el chatbot aún no termina."""
    return await servicio.explicacion(actor, id_caso)


@router.get("/{id_caso}/historial", response_model=list[EventoOut], responses=RESPUESTAS)
async def historial_del_caso(id_caso: str, actor: Usuario, servicio: Servicio) -> list[EventoOut]:
    """Línea de tiempo desde `eventos`. El técnico solo la ve en sus casos asignados; fuera de ellos, `404`."""
    return await servicio.historial(actor, id_caso)


@router.patch("/{id_caso}/asignacion", response_model=CasoResumen, responses=RESPUESTAS)
async def asignar_caso(id_caso: str, datos: AsignarIn, actor: SoloLider, servicio: Servicio) -> CasoResumen:
    """Pendiente de revisión → En revisión; reasignar un caso En revisión conserva el estado.

    El técnico debe estar activo y ser una cuenta `TEC-` (también la cuenta de técnico del líder). `409
    TRANSICION_INVALIDA` si el caso ya está Registrado, Cerrado o Descartado.
    """
    return await servicio.asignar(actor, id_caso, datos.tecnico_id)


@router.post("/{id_caso}/diagnostico", response_model=CasoDetalle, responses=RESPUESTAS)
async def registrar_diagnostico(
    id_caso: str, datos: DiagnosticoIn, actor: SoloTecnico, servicio: Servicio
) -> CasoDetalle:
    """Diagnóstico del técnico: completa la fila vacía y pasa el caso de En revisión a Registrado.

    Solo el técnico de la asignación vigente (el líder recibe `403`; otro técnico, `404`). `CONFIRMADO` solo si la
    identificación fue preliminar (P-04); `OTRA_PATOLOGIA` exige categoría y `REQUIERE_VISITA`, el motivo. Una
    categoría transmisible registrada evalúa las alertas.
    """
    return await servicio.diagnosticar(actor, id_caso, datos)


@router.post("/{id_caso}/cierre", response_model=CasoDetalle, responses=RESPUESTAS)
async def cerrar_caso(id_caso: str, actor: Usuario, servicio: Servicio) -> CasoDetalle:
    """Solo desde Registrado → Cerrado; `vence_en` = cierre + 6 meses. Un caso cerrado no se reabre."""
    return await servicio.cerrar(actor, id_caso)
