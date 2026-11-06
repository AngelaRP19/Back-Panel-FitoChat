"""Productores (guía §10.3). Solo el líder técnico: el técnico consulta los suyos en `/consulta/productores-fincas`."""

from datetime import UTC, date, datetime, time
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ErrorRespuesta
from app.api.paginacion import Pagina, ParametrosPagina, parametros_pagina
from app.core.catalogos_api import EspecieCultivo
from app.core.roles import Rol
from app.core.security_cognito import UsuarioActual, requiere_rol
from app.core.telefonos import celular_de_e164
from app.infrastructure.db.session import get_session
from app.modules.panel_api.cuentas.schemas import CambiarEstadoIn
from app.modules.panel_api.productores.repository import FiltrosProductores, ProductorFila
from app.modules.panel_api.productores.schemas import (
    FincaDeProductorOut,
    ProductorEditar,
    ProductorIn,
    ProductorOut,
)
from app.modules.panel_api.productores.service import ProductoresService

router = APIRouter(prefix="/productores", tags=["productores"])

RESPUESTAS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorRespuesta, "description": "Sin token, vencido o inválido."},
    403: {"model": ErrorRespuesta, "description": "Solo el líder técnico."},
    404: {"model": ErrorRespuesta, "description": "El productor no existe."},
    409: {"model": ErrorRespuesta, "description": "DUPLICADO: el celular ya está registrado."},
    422: {"model": ErrorRespuesta, "description": "VALIDACION: datos o filtros inválidos."},
}
_BOGOTA = ZoneInfo("America/Bogota")

SoloLider = Annotated[UsuarioActual, Depends(requiere_rol(Rol.LIDER_TECNICO))]


def get_servicio(session: Annotated[AsyncSession, Depends(get_session)]) -> ProductoresService:
    return ProductoresService(session)


Servicio = Annotated[ProductoresService, Depends(get_servicio)]


def a_productor_out(p: ProductorFila) -> ProductorOut:
    return ProductorOut(
        id=p.id,
        nombre=p.nombre,
        celular=celular_de_e164(p.celular_e164),
        activo=p.activo,
        vereda=p.fincas[0].vereda if p.fincas else None,
        fincas=[FincaDeProductorOut(id=f.id, nombre=f.nombre, vereda=f.vereda, especies=f.especies) for f in p.fincas],
        # La base guarda el día de registro (hora de Colombia): se entrega como el inicio de ese día, en UTC.
        registrado_en=datetime.combine(p.registro, time.min, tzinfo=_BOGOTA).astimezone(UTC),
        ultima_consulta_en=p.ultima_consulta,
        casos_ultimo_anio=p.casos_ultimo_anio,
        casos_activos=p.casos_activos,
    )


@router.get("", response_model=Pagina[ProductorOut], responses=RESPUESTAS)
async def listar_productores(
    _: SoloLider,
    servicio: Servicio,
    pagina: Annotated[ParametrosPagina, Depends(parametros_pagina)],
    q: Annotated[str | None, Query(max_length=100, description="Nombre o celular")] = None,
    estado: Literal["activo", "deshabilitado"] | None = None,
    vereda: Annotated[str | None, Query(max_length=100, description="Código, por ejemplo SAN_LUIS")] = None,
    especie: EspecieCultivo | None = None,
    con_casos: Annotated[Literal["con", "sin"] | None, Query(description="Con o sin casos activos")] = None,
    finca: Annotated[str | None, Query(max_length=100, description="Nombre de alguna de sus fincas")] = None,
    desde: Annotated[date | None, Query(description="Registrado desde (aaaa-mm-dd)")] = None,
    hasta: Annotated[date | None, Query(description="Registrado hasta (aaaa-mm-dd)")] = None,
    consulta: Annotated[
        Literal["30", "90"] | None, Query(description="30: consultó en los últimos 30 días; 90: sin consultas en 90")
    ] = None,
) -> Pagina[ProductorOut]:
    """Los filtros se combinan (AND). Ordenado por nombre, sin importar tildes."""
    filtros = FiltrosProductores(
        q=q,
        estado=estado,
        vereda=vereda,
        especie=especie.value if especie else None,
        con_casos=con_casos,
        finca=finca,
        desde=desde,
        hasta=hasta,
        consulta=consulta,
    )
    filas, total = await servicio.listar(pagina, filtros)
    return Pagina[ProductorOut](
        items=[a_productor_out(f) for f in filas], total=total, page=pagina.page, page_size=pagina.page_size
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ProductorOut, responses=RESPUESTAS)
async def crear_productor(datos: ProductorIn, actor: SoloLider, servicio: Servicio) -> ProductorOut:
    """Registro desde el panel (RQF07). El chatbot también crea productores en el primer contacto (P-B08)."""
    return a_productor_out(await servicio.crear(datos, actor))


@router.get("/{id_productor}", response_model=ProductorOut, responses=RESPUESTAS)
async def obtener_productor(id_productor: str, _: SoloLider, servicio: Servicio) -> ProductorOut:
    return a_productor_out(await servicio.obtener(id_productor))


@router.patch("/{id_productor}", response_model=ProductorOut, responses=RESPUESTAS)
async def editar_productor(
    id_productor: str, datos: ProductorEditar, actor: SoloLider, servicio: Servicio
) -> ProductorOut:
    return a_productor_out(await servicio.editar(id_productor, datos, actor))


@router.patch("/{id_productor}/estado", response_model=ProductorOut, responses=RESPUESTAS)
async def cambiar_estado_productor(
    id_productor: str, datos: CambiarEstadoIn, actor: SoloLider, servicio: Servicio
) -> ProductorOut:
    """Un productor deshabilitado no inicia casos ni recibe mensajes; sus casos se conservan."""
    return a_productor_out(await servicio.cambiar_estado(id_productor, datos.activo, actor))
