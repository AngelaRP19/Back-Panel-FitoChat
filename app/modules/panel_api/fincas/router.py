"""Fincas, cultivos y catálogos del territorio (guía §10.3). Fincas y cultivos son solo del líder técnico."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ErrorRespuesta
from app.api.paginacion import Pagina, ParametrosPagina, parametros_pagina
from app.core.catalogos_api import CATEGORIAS, ESPECIES, EspecieCultivo, TipoCultivo, normalizar_codigo
from app.core.roles import Rol
from app.core.security_cognito import UsuarioActual, requiere_rol
from app.infrastructure.db.session import get_session
from app.modules.panel_api.fincas import repository as repo_fincas
from app.modules.panel_api.fincas.repository import CultivoFila, FiltrosFincas, FincaFila
from app.modules.panel_api.fincas.schemas import (
    LATITUD_ESPERADA,
    CategoriaOut,
    CodigoNombre,
    CultivoEditar,
    CultivoIn,
    CultivoOut,
    FincaEditar,
    FincaIn,
    FincaOut,
    ProductorDeFincaOut,
)
from app.modules.panel_api.fincas.service import FincasService
from app.modules.panel_api.territorio_comun import veredas_registradas

RESPUESTAS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorRespuesta, "description": "Sin token, vencido o inválido."},
    403: {"model": ErrorRespuesta, "description": "Solo el líder técnico."},
    404: {"model": ErrorRespuesta, "description": "La finca o el cultivo no existe."},
    409: {"model": ErrorRespuesta, "description": "DUPLICADO o ESPECIE_EN_USO (con `detalle` de los casos)."},
    422: {"model": ErrorRespuesta, "description": "VALIDACION: datos o filtros inválidos."},
}

SoloLider = Annotated[UsuarioActual, Depends(requiere_rol(Rol.LIDER_TECNICO))]
CualquierRol = Annotated[UsuarioActual, Depends(requiere_rol(Rol.LIDER_TECNICO, Rol.TECNICO))]


def get_servicio(session: Annotated[AsyncSession, Depends(get_session)]) -> FincasService:
    return FincasService(session)


Servicio = Annotated[FincasService, Depends(get_servicio)]


def a_cultivo_out(c: CultivoFila) -> CultivoOut:
    return CultivoOut(
        id=c.id,
        nombre=c.nombre,
        tipo=TipoCultivo(c.tipo),
        especies=c.especies,
        casos_activos=c.casos_activos,
    )


def advertencias(latitud: float, longitud: float) -> list[str]:
    """Coordenadas fuera de lo esperado para Zetaquirá: se avisa, pero no se bloquea."""
    avisos: list[str] = []
    if not LATITUD_ESPERADA[0] <= latitud <= LATITUD_ESPERADA[1]:
        avisos.append("La latitud está fuera de la zona de Zetaquirá (entre 5,20 y 5,30). Revísala.")
    if longitud >= 0:
        avisos.append("La longitud de Colombia es negativa (por ejemplo -73,17). Revísala.")
    return avisos


def a_finca_out(f: FincaFila) -> FincaOut:
    latitud, longitud = float(f.latitud), float(f.longitud)
    return FincaOut(
        id=f.id,
        nombre=f.nombre,
        vereda=normalizar_codigo(f.vereda),
        latitud=latitud,
        longitud=longitud,
        productor=ProductorDeFincaOut(id=f.id_productor, nombre=f.nombre_productor, activo=f.productor_activo),
        cultivos=[a_cultivo_out(c) for c in f.cultivos],
        casos_activos=f.casos_activos,
        advertencias=advertencias(latitud, longitud),
    )


router = APIRouter(tags=["fincas y cultivos"])


@router.get("/fincas", response_model=Pagina[FincaOut], responses=RESPUESTAS)
async def listar_fincas(
    _: SoloLider,
    servicio: Servicio,
    pagina: Annotated[ParametrosPagina, Depends(parametros_pagina)],
    q: Annotated[str | None, Query(max_length=100, description="Nombre de la finca o del productor")] = None,
    vereda: Annotated[str | None, Query(max_length=100, description="Código, por ejemplo SAN_LUIS")] = None,
    especie: EspecieCultivo | None = None,
    tipo: TipoCultivo | None = None,
    casos: Annotated[Literal["con", "sin"] | None, Query(description="Con o sin casos activos")] = None,
    productor_id: str | None = None,
) -> Pagina[FincaOut]:
    """Agrupadas por vereda y, dentro de cada una, por nombre. Los filtros se combinan (AND)."""
    filtros = FiltrosFincas(
        q=q,
        vereda=vereda,
        especie=especie.value if especie else None,
        tipo=tipo.value if tipo else None,
        casos=casos,
        productor_id=productor_id,
    )
    filas, total = await servicio.listar(pagina, filtros)
    return Pagina[FincaOut](
        items=[a_finca_out(f) for f in filas], total=total, page=pagina.page, page_size=pagina.page_size
    )


@router.post("/fincas", status_code=status.HTTP_201_CREATED, response_model=FincaOut, responses=RESPUESTAS)
async def crear_finca(datos: FincaIn, actor: SoloLider, servicio: Servicio) -> FincaOut:
    """Solo para un productor activo (RQF14). Las coordenadas raras se avisan en `advertencias`, sin bloquear."""
    return a_finca_out(await servicio.crear(datos, actor))


class ResumenFincas(BaseModel):
    fincas: int
    cultivos: int
    veredas: list[str] = Field(description="Nombres de las veredas con fincas registradas")


@router.get("/fincas/resumen", response_model=ResumenFincas, responses=RESPUESTAS)
async def resumen_fincas(_: SoloLider, servicio: Servicio) -> ResumenFincas:
    """B-05: totales para la cabecera de la pantalla de fincas."""
    fincas, cultivos, veredas = await repo_fincas.resumen(servicio.session)
    return ResumenFincas(fincas=fincas, cultivos=cultivos, veredas=veredas)


@router.get("/fincas/{id_finca}", response_model=FincaOut, responses=RESPUESTAS)
async def obtener_finca(id_finca: str, _: SoloLider, servicio: Servicio) -> FincaOut:
    return a_finca_out(await servicio.obtener(id_finca))


@router.patch("/fincas/{id_finca}", response_model=FincaOut, responses=RESPUESTAS)
async def editar_finca(id_finca: str, datos: FincaEditar, actor: SoloLider, servicio: Servicio) -> FincaOut:
    """Datos de la finca y, opcionalmente, tipo y especies de sus cultivos. Quitar una especie en uso → `409`."""
    return a_finca_out(await servicio.editar(id_finca, datos, actor))


@router.get("/fincas/{id_finca}/cultivos", response_model=list[CultivoOut], responses=RESPUESTAS)
async def listar_cultivos(id_finca: str, _: SoloLider, servicio: Servicio) -> list[CultivoOut]:
    return [a_cultivo_out(c) for c in await servicio.cultivos(id_finca)]


@router.post(
    "/fincas/{id_finca}/cultivos", status_code=status.HTTP_201_CREATED, response_model=FincaOut, responses=RESPUESTAS
)
async def crear_cultivo(id_finca: str, datos: CultivoIn, actor: SoloLider, servicio: Servicio) -> FincaOut:
    """Agrega un cultivo (con al menos una especie) y responde con la finca actualizada (RQF17)."""
    return a_finca_out(await servicio.crear_cultivo(id_finca, datos, actor))


@router.patch("/cultivos/{id_cultivo}", response_model=CultivoOut, responses=RESPUESTAS)
async def editar_cultivo(id_cultivo: str, datos: CultivoEditar, actor: SoloLider, servicio: Servicio) -> CultivoOut:
    """Nombre, tipo y especies. Quitar una especie que usan casos → `409 ESPECIE_EN_USO` con los casos afectados."""
    return a_cultivo_out(await servicio.editar_cultivo(id_cultivo, datos, actor))


# --- Catálogos (guía §6.2): los consultan el líder y el técnico ------------------------------------------------------

catalogos = APIRouter(tags=["catálogos"])
_RESPUESTAS_CATALOGO: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorRespuesta, "description": "Sin token, vencido o inválido."},
    403: {"model": ErrorRespuesta, "description": "Cuenta inactiva."},
}


@catalogos.get("/especies", response_model=list[CodigoNombre], responses=_RESPUESTAS_CATALOGO)
async def listar_especies(
    _: CualquierRol, session: Annotated[AsyncSession, Depends(get_session)]
) -> list[CodigoNombre]:
    nombres = {n for (n,) in await session.execute(text("SELECT nombre_especie FROM especies"))}
    return [
        CodigoNombre(codigo=c, nombre=ESPECIES.a_nombre(c)) for c in ESPECIES.codigos if ESPECIES.a_nombre(c) in nombres
    ]


@catalogos.get("/categorias", response_model=list[CategoriaOut], responses=_RESPUESTAS_CATALOGO)
async def listar_categorias(
    _: CualquierRol, session: Annotated[AsyncSession, Depends(get_session)]
) -> list[CategoriaOut]:
    filas = {
        nombre: es == "S"
        for nombre, es in await session.execute(
            text("SELECT nombre_categoria, es_transmisible FROM categorias_fitosanitarias")
        )
    }
    return [
        CategoriaOut(codigo=c, nombre=CATEGORIAS.a_nombre(c), es_transmisible=filas[CATEGORIAS.a_nombre(c)])
        for c in CATEGORIAS.codigos
        if CATEGORIAS.a_nombre(c) in filas
    ]


@catalogos.get("/veredas", response_model=list[CodigoNombre], responses=_RESPUESTAS_CATALOGO)
async def listar_veredas(_: CualquierRol, session: Annotated[AsyncSession, Depends(get_session)]) -> list[CodigoNombre]:
    """No hay catálogo: son las veredas de las fincas registradas, con su código normalizado."""
    veredas = await veredas_registradas(session)
    return [CodigoNombre(codigo=c, nombre=n) for c, n in sorted(veredas.items(), key=lambda kv: kv[1].lower())]
