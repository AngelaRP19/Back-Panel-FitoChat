"""Resumen y navegación (guía §10.6): contadores del Sidebar. `resumen/inicio` es de la Fase B (P-01)."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.errors import ErrorRespuesta
from app.core.roles import Rol
from app.core.security_cognito import UsuarioActual, requiere_rol
from app.modules.panel_api.casos.router import Servicio
from app.modules.panel_api.casos.schemas import NavegacionOut

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
