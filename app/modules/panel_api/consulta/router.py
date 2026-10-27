"""Consulta de productores y fincas para el técnico (guía §10.3, P-07).

El técnico no ve el directorio de productores: solo los de los casos que tiene asignados. El líder usa
`/productores` y `/fincas`.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError, ErrorRespuesta
from app.api.paginacion import Pagina, ParametrosPagina, parametros_pagina
from app.core.catalogos_api import EspecieCultivo, normalizar_codigo
from app.core.codigos import codigo_caso
from app.core.roles import Rol
from app.core.security_cognito import UsuarioActual, requiere_rol
from app.core.telefonos import celular_de_e164
from app.infrastructure.db.session import get_session
from app.modules.panel_api.consulta import repository as repo
from app.modules.panel_api.consulta.repository import FiltrosConsulta, RegistroFila

router = APIRouter(prefix="/consulta", tags=["consulta del técnico"])

RESPUESTAS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorRespuesta, "description": "Sin token, vencido o inválido."},
    403: {"model": ErrorRespuesta, "description": "Solo el técnico; el líder usa /productores y /fincas."},
    404: {"model": ErrorRespuesta, "description": "No está entre los casos del técnico."},
    422: {"model": ErrorRespuesta, "description": "VALIDACION: filtros inválidos."},
}

SoloTecnico = Annotated[UsuarioActual, Depends(requiere_rol(Rol.TECNICO))]


class ProductorConsultaOut(BaseModel):
    id: str
    nombre: str
    celular: str = Field(pattern=r"^3\d{9}$")


class FincaConsultaOut(BaseModel):
    id: str
    nombre: str
    vereda: str
    latitud: float
    longitud: float


class CultivoConsultaOut(BaseModel):
    id: str
    nombre: str
    especies: list[str]


class CasoConsultaOut(BaseModel):
    id: str = Field(examples=["CAS-00000241"])
    codigo: str = Field(examples=["#0241"])
    estado: str


class RegistroConsultaOut(BaseModel):
    id: str = Field(description="`<id del productor>__<id de la finca>`", examples=["PRO-000001__FIN-000001"])
    productor: ProductorConsultaOut
    finca: FincaConsultaOut
    cultivos: list[CultivoConsultaOut] = Field(description="Solo los cultivos de sus casos")
    casos: list[CasoConsultaOut]


class ResumenConsultaOut(BaseModel):
    productores: int
    fincas: int
    casos: int


class PaginaConsultaOut(Pagina[RegistroConsultaOut]):
    resumen: ResumenConsultaOut


def a_registro_out(r: RegistroFila) -> RegistroConsultaOut:
    return RegistroConsultaOut(
        id=r.id,
        productor=ProductorConsultaOut(
            id=r.id_productor, nombre=r.nombre_productor, celular=celular_de_e164(r.celular_e164)
        ),
        finca=FincaConsultaOut(
            id=r.id_finca,
            nombre=r.nombre_finca,
            vereda=normalizar_codigo(r.vereda),
            latitud=float(r.latitud),
            longitud=float(r.longitud),
        ),
        cultivos=[CultivoConsultaOut(id=c.id, nombre=c.nombre, especies=c.especies) for c in r.cultivos],
        casos=[CasoConsultaOut(id=c.id, codigo=codigo_caso(c.id), estado=c.estado) for c in r.casos],
    )


@router.get("/productores-fincas", response_model=PaginaConsultaOut, responses=RESPUESTAS)
async def listar_productores_fincas(
    tecnico: SoloTecnico,
    session: Annotated[AsyncSession, Depends(get_session)],
    pagina: Annotated[ParametrosPagina, Depends(parametros_pagina)],
    q: Annotated[str | None, Query(max_length=100, description="Productor, finca o número de caso")] = None,
    vereda: Annotated[str | None, Query(max_length=100, description="Código, por ejemplo SAN_LUIS")] = None,
    especie: EspecieCultivo | None = None,
    estado_casos: Annotated[
        Literal["pendientes", "cerrados"] | None, Query(description="Con casos abiertos, o solo con casos terminados")
    ] = None,
) -> PaginaConsultaOut:
    """Productor + finca de los casos asignados al técnico, con los cultivos y casos que lo relacionan."""
    filtros = FiltrosConsulta(q=q, vereda=vereda, especie=especie.value if especie else None, estado_casos=estado_casos)
    filas, total = await repo.listar(session, tecnico.id, pagina, filtros)
    totales = await repo.resumen(session, tecnico.id)
    return PaginaConsultaOut(
        items=[a_registro_out(f) for f in filas],
        total=total,
        page=pagina.page,
        page_size=pagina.page_size,
        resumen=ResumenConsultaOut(productores=totales.productores, fincas=totales.fincas, casos=totales.casos),
    )


@router.get("/productores-fincas/{id_registro}", response_model=RegistroConsultaOut, responses=RESPUESTAS)
async def obtener_productor_finca(
    id_registro: str, tecnico: SoloTecnico, session: Annotated[AsyncSession, Depends(get_session)]
) -> RegistroConsultaOut:
    registro = await repo.obtener(session, tecnico.id, id_registro)
    if registro is None:  # lo que no está en sus casos no existe para el técnico
        raise ApiError(404, "NO_ENCONTRADO", "No encontrado.")
    return a_registro_out(registro)
