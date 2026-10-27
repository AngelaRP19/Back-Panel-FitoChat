"""Resumen y navegación (guía §10.6): contadores del Sidebar e indicadores del Inicio (Fase B, P-01)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ErrorRespuesta
from app.core.roles import Rol
from app.core.security_cognito import UsuarioActual, requiere_rol
from app.infrastructure.db.session import get_session
from app.modules.panel_api.casos.router import Servicio
from app.modules.panel_api.casos.schemas import NavegacionOut
from app.modules.panel_api.resumen.schemas import CasosPorCategoria, Inicio, InicioLider, InicioTecnico, Periodo
from app.modules.panel_api.resumen.service import ResumenService

router = APIRouter(prefix="/resumen", tags=["resumen"])

Usuario = Annotated[UsuarioActual, Depends(requiere_rol(Rol.LIDER_TECNICO, Rol.TECNICO))]


@router.get(
    "/navegacion",
    response_model=NavegacionOut,
    responses={401: {"model": ErrorRespuesta}, 403: {"model": ErrorRespuesta}},
)
async def navegacion(actor: Usuario, servicio: Servicio) -> NavegacionOut:
    """Contadores del Sidebar. El técnico ve solo lo suyo: sus casos En revisión y ninguna alerta."""
    pendientes, alertas = await servicio.navegacion(actor)
    return NavegacionOut(pendientes=pendientes, alertas_generadas=alertas)


def get_servicio_resumen(session: Annotated[AsyncSession, Depends(get_session)]) -> ResumenService:
    return ResumenService(session)


ServicioResumen = Annotated[ResumenService, Depends(get_servicio_resumen)]
SoloLider = Annotated[UsuarioActual, Depends(requiere_rol(Rol.LIDER_TECNICO))]
_RESPUESTAS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorRespuesta, "description": "Sin token, vencido o inválido."},
    403: {"model": ErrorRespuesta, "description": "Solo el líder técnico."},
    422: {"model": ErrorRespuesta, "description": "VALIDACION: periodo inválido."},
}


@router.get("/inicio", response_model=Inicio, responses=_RESPUESTAS)
async def inicio(actor: Usuario, servicio: ServicioResumen) -> InicioLider | InicioTecnico:
    """Indicadores de la pantalla de Inicio. La forma depende del rol (`rol` la distingue): el líder ve el panorama
    de todo el sistema y el técnico, solo contadores de lo suyo."""
    return await servicio.inicio(actor)


@router.get("/casos-por-categoria", response_model=CasosPorCategoria, responses=_RESPUESTAS)
async def casos_por_categoria(_: SoloLider, servicio: ServicioResumen, periodo: Periodo = "90d") -> CasosPorCategoria:
    """Diagnósticos confirmados por categoría en los últimos 30 o 90 días, o en lo que va del año."""
    return await servicio.por_categoria(periodo)
