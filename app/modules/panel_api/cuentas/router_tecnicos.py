"""Cuentas del panel (guía §10.2): líderes y técnicos. Todo es solo del líder técnico."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status

from app.api.paginacion import Pagina, ParametrosPagina, parametros_pagina
from app.core.roles import Rol
from app.modules.panel_api.cuentas.dependencias import (
    RESPUESTAS_AUTENTICACION,
    RESPUESTAS_ESCRITURA,
    ServicioCuentas,
    SoloLider,
    a_carga_out,
    a_cuenta_out,
)
from app.modules.panel_api.cuentas.schemas import (
    CambiarEstadoIn,
    CargaTecnicoOut,
    CuentaEditar,
    CuentaIn,
    CuentaOut,
)

router = APIRouter(prefix="/tecnicos", tags=["técnicos"])


@router.get("", response_model=Pagina[CuentaOut] | list[CargaTecnicoOut], responses=RESPUESTAS_AUTENTICACION)
async def listar_cuentas(
    _: SoloLider,
    servicio: ServicioCuentas,
    pagina: Annotated[ParametrosPagina, Depends(parametros_pagina)],
    q: Annotated[str | None, Query(max_length=100, description="Nombre o correo, sin importar tildes")] = None,
    rol: Rol | None = None,
    activo: bool | None = None,
    estado: Annotated[
        Literal["activa", "deshabilitada"] | None,
        Query(description="Filtro del panel: equivale a activo=true / activo=false"),
    ] = None,
    carga: Annotated[
        Literal["pendientes", "sin"] | None, Query(description="Con casos En revisión, o sin casos")
    ] = None,
    con_carga: Annotated[bool, Query(description="Lista para asignar casos: técnicos activos con su carga")] = False,
) -> Pagina[CuentaOut] | list[CargaTecnicoOut]:
    """Líderes y técnicos juntos (los líderes primero). Con `con_carga=true`, solo los técnicos activos y su carga."""
    if con_carga:
        return [a_carga_out(c) for c in await servicio.tecnicos_con_carga()]
    if estado is not None and activo is None:
        activo = estado == "activa"
    filas, total = await servicio.listar(pagina, q=q, rol=rol, activo=activo, carga=carga)
    return Pagina[CuentaOut](
        items=[a_cuenta_out(f) for f in filas], total=total, page=pagina.page, page_size=pagina.page_size
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CuentaOut, responses=RESPUESTAS_ESCRITURA)
async def crear_cuenta(datos: CuentaIn, actor: SoloLider, servicio: ServicioCuentas) -> CuentaOut:
    """Crea la cuenta en Cognito y en la base. La persona recibe una contraseña temporal en su correo (RQF11)."""
    return a_cuenta_out(await servicio.crear(datos, actor))


@router.get("/{id_cuenta}", response_model=CuentaOut, responses=RESPUESTAS_ESCRITURA)
async def obtener_cuenta(id_cuenta: str, _: SoloLider, servicio: ServicioCuentas) -> CuentaOut:
    return a_cuenta_out(await servicio.obtener(id_cuenta))


@router.patch("/{id_cuenta}", response_model=CuentaOut, responses=RESPUESTAS_ESCRITURA)
async def editar_cuenta(id_cuenta: str, datos: CuentaEditar, actor: SoloLider, servicio: ServicioCuentas) -> CuentaOut:
    """Edita nombre, correo o celular de un técnico (RQF05). El líder se edita solo desde `PATCH /me` (P-11)."""
    return a_cuenta_out(await servicio.editar(id_cuenta, datos, actor))


@router.patch("/{id_cuenta}/estado", response_model=CuentaOut, responses=RESPUESTAS_ESCRITURA)
async def cambiar_estado_de_cuenta(
    id_cuenta: str, datos: CambiarEstadoIn, actor: SoloLider, servicio: ServicioCuentas
) -> CuentaOut:
    """Habilita o deshabilita. `409 UNICA_CUENTA_ACTIVA` si es la única cuenta activa de su rol (RQF12, RQF13)."""
    return a_cuenta_out(await servicio.cambiar_estado(id_cuenta, datos.activo, actor))
