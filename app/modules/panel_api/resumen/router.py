"""Resumen y navegación (guía §10.6): contadores del Sidebar e indicadores del Inicio (Fase B, P-01)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ErrorRespuesta
from app.core.roles import Rol
from app.core.security_cognito import UsuarioActual, requiere_rol
from app.infrastructure.db.session import get_session
from app.modules.panel_api.casos.router import Servicio
from app.modules.panel_api.casos.schemas import LiderRef, NavegacionOut
from app.modules.panel_api.notificaciones.router import Notificaciones
from app.modules.panel_api.resumen import repository as repo_resumen
from app.modules.panel_api.resumen.schemas import CasosPorCategoria, Inicio, InicioLider, InicioTecnico, Periodo
from app.modules.panel_api.resumen.service import ResumenService

router = APIRouter(prefix="/resumen", tags=["resumen"])

Usuario = Annotated[UsuarioActual, Depends(requiere_rol(Rol.LIDER_TECNICO, Rol.TECNICO))]


@router.get(
    "/navegacion",
    response_model=NavegacionOut,
    responses={401: {"model": ErrorRespuesta}, 403: {"model": ErrorRespuesta}},
)
async def navegacion(actor: Usuario, servicio: Servicio, notificaciones: Notificaciones) -> NavegacionOut:
    """Contadores del Sidebar y de la campana. El técnico ve solo lo suyo: sus casos En revisión y ninguna alerta."""
    pendientes, alertas = await servicio.navegacion(actor)
    lider = await repo_resumen.nombre_de_lider(servicio.session)
    return NavegacionOut(
        casos_pendientes=pendientes,
        alertas_generadas=alertas,
        notificaciones_nuevas=await notificaciones.contar_nuevas(actor.id),
        lider=LiderRef(nombre=lider) if lider else None,
    )


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


# --- Rutas que usa el panel (B-02): una por pantalla de Inicio, con la forma de `features/inicio/types.ts` ---------

panel = APIRouter(prefix="/panel", tags=["resumen"])
SoloTecnico = Annotated[UsuarioActual, Depends(requiere_rol(Rol.TECNICO))]
_RESPUESTAS_TECNICO = {**_RESPUESTAS, 403: {"model": ErrorRespuesta, "description": "Solo el técnico."}}


@panel.get("/resumen", response_model=InicioLider, responses=_RESPUESTAS)
async def resumen_lider(actor: SoloLider, servicio: ServicioResumen) -> InicioLider:
    """Inicio del líder (pantalla 16): panorama de todo el sistema."""
    resultado = await servicio.inicio(actor)
    assert isinstance(resultado, InicioLider)
    return resultado


@panel.get("/resumen-tecnico", response_model=InicioTecnico, responses=_RESPUESTAS_TECNICO)
async def resumen_tecnico(actor: SoloTecnico, servicio: ServicioResumen) -> InicioTecnico:
    """Inicio del técnico (pantalla 17): solo sus casos."""
    resultado = await servicio.inicio(actor)
    assert isinstance(resultado, InicioTecnico)
    return resultado


@panel.get("/casos-por-categoria", response_model=CasosPorCategoria, responses=_RESPUESTAS)
async def panel_casos_por_categoria(
    _: SoloLider, servicio: ServicioResumen, periodo: Periodo = "90d"
) -> CasosPorCategoria:
    """Igual que /resumen/casos-por-categoria, con la ruta que usa el panel."""
    return await servicio.por_categoria(periodo)
