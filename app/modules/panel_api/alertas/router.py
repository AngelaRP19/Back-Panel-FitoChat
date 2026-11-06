"""Alertas epidemiológicas del panel (guía §10.5).

El líder confirma, descarta, redacta, comunica y corrige; el técnico solo consulta (y puede marcar «seguir», P-06).
Las rutas fijas (`/parametros`) van antes de `/{id_alerta}` para que no se confundan con un id.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError, ErrorRespuesta
from app.api.paginacion import ParametrosPagina, parametros_pagina
from app.core.catalogos_api import CategoriaFito, EstadoAlerta
from app.core.roles import Rol
from app.core.security_cognito import UsuarioActual, requiere_rol
from app.infrastructure.db.session import get_session
from app.infrastructure.whatsapp_client import WhatsAppClient, get_whatsapp_client
from app.modules.panel_api.alertas.schemas import (
    AlertaDetalle,
    BorradorIn,
    ComunicarIn,
    CorregirIn,
    DescartarIn,
    DestinatariosVereda,
    PaginaAlertas,
    ParametroCategoria,
    ParametrosIn,
)
from app.modules.panel_api.alertas.service import AlertasService

router = APIRouter(prefix="/alertas", tags=["alertas"])

RESPUESTAS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorRespuesta, "description": "Sin token, vencido o inválido."},
    403: {"model": ErrorRespuesta, "description": "Solo el líder técnico (o solo el técnico, en «seguir»)."},
    404: {"model": ErrorRespuesta, "description": "La alerta no existe."},
    409: {"model": ErrorRespuesta, "description": "TRANSICION_INVALIDA: el estado de la alerta no lo permite."},
    422: {"model": ErrorRespuesta, "description": "VALIDACION: mensaje vacío, largo o con tratamientos, etc."},
}

Usuario = Annotated[UsuarioActual, Depends(requiere_rol(Rol.LIDER_TECNICO, Rol.TECNICO))]
SoloLider = Annotated[UsuarioActual, Depends(requiere_rol(Rol.LIDER_TECNICO))]
SoloTecnico = Annotated[UsuarioActual, Depends(requiere_rol(Rol.TECNICO))]


def get_whatsapp() -> WhatsAppClient:
    try:
        return get_whatsapp_client()
    except RuntimeError:
        raise ApiError(503, "ERROR_INTERNO", "El envío por WhatsApp no está disponible por ahora.") from None


def get_servicio(
    session: Annotated[AsyncSession, Depends(get_session)], whatsapp: Annotated[WhatsAppClient, Depends(get_whatsapp)]
) -> AlertasService:
    return AlertasService(session, whatsapp)


Servicio = Annotated[AlertasService, Depends(get_servicio)]


# --- Parámetros (antes de /{id_alerta}) -------------------------------------------------------------------------------


@router.get("/parametros", response_model=list[ParametroCategoria], responses=RESPUESTAS)
async def listar_parametros(_: Usuario, servicio: Servicio) -> list[ParametroCategoria]:
    """Parámetros por categoría; `habilitada` = categoría transmisible. `actualizado_por` sale de la auditoría."""
    return await servicio.parametros()


@router.put("/parametros/{categoria}", response_model=ParametroCategoria, responses=RESPUESTAS)
async def guardar_parametros(
    categoria: CategoriaFito, datos: ParametrosIn, actor: SoloLider, servicio: Servicio
) -> ParametroCategoria:
    """Solo para categorías transmisibles. Aplica a las alertas que se evalúen desde ahora; las ya generadas conservan
    los parámetros con que nacieron."""
    return await servicio.guardar_parametros(actor, categoria.value, datos)


# --- Alertas -------------------------------------------------------------------------------------------------------


@router.get("", response_model=PaginaAlertas, responses=RESPUESTAS)
async def listar_alertas(
    _: Usuario,
    servicio: Servicio,
    pagina: Annotated[ParametrosPagina, Depends(parametros_pagina)],
    estado: EstadoAlerta | None = None,
) -> PaginaAlertas:
    """Más recientes primero, con `conteos` por estado para las pestañas."""
    return await servicio.listar(estado, pagina)


@router.get("/{id_alerta}", response_model=AlertaDetalle, responses=RESPUESTAS)
async def obtener_alerta(id_alerta: str, actor: Usuario, servicio: Servicio) -> AlertaDetalle:
    """Casos que la originan, categoría, parámetros con que se generó y veredas."""
    return await servicio.detalle(actor, id_alerta)


@router.post("/{id_alerta}/confirmar", response_model=AlertaDetalle, responses=RESPUESTAS)
async def confirmar_alerta(id_alerta: str, actor: SoloLider, servicio: Servicio) -> AlertaDetalle:
    """GENERADA o EN_REVISION → CONFIRMADA."""
    return await servicio.confirmar(actor, id_alerta)


@router.post("/{id_alerta}/descartar", response_model=AlertaDetalle, responses=RESPUESTAS)
async def descartar_alerta(id_alerta: str, datos: DescartarIn, actor: SoloLider, servicio: Servicio) -> AlertaDetalle:
    """GENERADA o EN_REVISION → DESCARTADA, con justificación obligatoria."""
    return await servicio.descartar(actor, id_alerta, datos.justificacion)


@router.get("/{id_alerta}/destinatarios", response_model=list[DestinatariosVereda], responses=RESPUESTAS)
async def destinatarios_de_alerta(
    id_alerta: str,
    _: SoloLider,
    servicio: Servicio,
    vereda: Annotated[str | None, Query(max_length=100, description="Código, por ejemplo SAN_LUIS")] = None,
) -> list[DestinatariosVereda]:
    """Productores dentro de `distancia_km` de las fincas de los casos (`fn_distancia_km`), agrupados por vereda."""
    return await servicio.destinatarios(id_alerta, vereda)


@router.put("/{id_alerta}/borrador", response_model=AlertaDetalle, responses=RESPUESTAS)
async def guardar_borrador(id_alerta: str, datos: BorradorIn, actor: SoloLider, servicio: Servicio) -> AlertaDetalle:
    """Guarda la recomendación (máximo 300 caracteres, sin productos ni dosis) sin enviarla. Solo CONFIRMADA."""
    return await servicio.guardar_borrador(actor, id_alerta, datos.mensaje)


@router.post("/{id_alerta}/comunicar", response_model=AlertaDetalle, responses=RESPUESTAS)
async def comunicar_alerta(id_alerta: str, datos: ComunicarIn, actor: SoloLider, servicio: Servicio) -> AlertaDetalle:
    """CONFIRMADA → COMUNICADA. Envía por WhatsApp (simulado hasta la Fase C) y registra el resultado por destinatario.

    Reintento seguro: no reenvía a quien ya recibió. Con la alerta ya COMUNICADA solo reintenta los envíos fallidos.
    """
    return await servicio.comunicar(actor, id_alerta, datos)


@router.post("/{id_alerta}/corregir", response_model=AlertaDetalle, responses=RESPUESTAS)
async def corregir_alerta(id_alerta: str, datos: CorregirIn, actor: SoloLider, servicio: Servicio) -> AlertaDetalle:
    """COMUNICADA → CORREGIDA, una sola vez (D-17): plantilla fija más la explicación, solo a quienes la recibieron."""
    return await servicio.corregir(actor, id_alerta, datos)


@router.post("/{id_alerta}/seguir", response_model=AlertaDetalle, responses=RESPUESTAS)
async def seguir_alerta(id_alerta: str, actor: SoloTecnico, servicio: Servicio) -> AlertaDetalle:
    """«Avisarme cuando cambie» (P-06; el aviso en sí está por definir). Se anota en la auditoría."""
    await servicio.seguir(actor, id_alerta, True)
    return await servicio.detalle(actor, id_alerta)  # el panel espera la alerta completa


@router.delete("/{id_alerta}/seguir", response_model=AlertaDetalle, responses=RESPUESTAS)
async def dejar_de_seguir_alerta(id_alerta: str, actor: SoloTecnico, servicio: Servicio) -> AlertaDetalle:
    await servicio.seguir(actor, id_alerta, False)
    return await servicio.detalle(actor, id_alerta)
