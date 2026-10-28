"""Campana del panel (pantalla 18 · B-10): GET /notificaciones y POST /notificaciones/leidas, sin preferencias."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field

from app.api.errors import ErrorRespuesta
from app.core.roles import Rol
from app.core.security_cognito import UsuarioActual, requiere_rol
from app.modules.panel_api.notificaciones.service import Notificador, get_notificador

router = APIRouter(prefix="/notificaciones", tags=["notificaciones"])

Usuario = Annotated[UsuarioActual, Depends(requiere_rol(Rol.LIDER_TECNICO, Rol.TECNICO))]
Notificaciones = Annotated[Notificador, Depends(get_notificador)]
_RESPUESTAS: dict[int | str, dict[str, object]] = {401: {"model": ErrorRespuesta}, 403: {"model": ErrorRespuesta}}


class NotificacionOut(BaseModel):
    id: str = Field(examples=["NOT-3FA9C1D2E4"])
    tipo: str
    titulo: str
    texto: str
    enlace: str = Field(description="Ruta del panel a la que lleva el aviso")
    en: datetime
    leida: bool


class ListaNotificaciones(BaseModel):
    items: list[NotificacionOut] = Field(description="Últimos 7 días, de la más reciente a la más antigua")
    nuevas: int


@router.get("", response_model=ListaNotificaciones, responses=_RESPUESTAS)
async def listar_notificaciones(actor: Usuario, notificador: Notificaciones) -> ListaNotificaciones:
    """Avisos de los últimos 7 días de la cuenta de la sesión."""
    items = [
        NotificacionOut(id=n.id, tipo=n.tipo, titulo=n.titulo, texto=n.texto, enlace=n.enlace, en=n.en, leida=n.leida)
        for n in await notificador.listar(actor.id)
    ]
    return ListaNotificaciones(items=items, nuevas=sum(not n.leida for n in items))


@router.post("/leidas", status_code=status.HTTP_204_NO_CONTENT, responses=_RESPUESTAS)
async def marcar_leidas(actor: Usuario, notificador: Notificaciones) -> Response:
    """«Marcar como leídas»: todas las de la cuenta."""
    await notificador.marcar_leidas(actor.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
