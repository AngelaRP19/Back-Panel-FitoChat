from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ErrorRespuesta
from app.core.roles import Rol
from app.core.security_cognito import UsuarioActual, requiere_rol
from app.infrastructure.cognito_admin_client import CognitoAdmin, get_cognito_admin
from app.infrastructure.db.session import get_session
from app.modules.panel_api.cuentas.repository import CuentaFila
from app.modules.panel_api.cuentas.schemas import CargaTecnicoOut, CuentaOut
from app.modules.panel_api.cuentas.service import CuentasService, a_celular_api

RESPUESTAS_AUTENTICACION: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorRespuesta, "description": "Sin token, vencido o inválido."},
    403: {"model": ErrorRespuesta, "description": "Cuenta inactiva o sin permiso."},
}
RESPUESTAS_ESCRITURA: dict[int | str, dict[str, object]] = {
    **RESPUESTAS_AUTENTICACION,
    404: {"model": ErrorRespuesta, "description": "La cuenta no existe."},
    409: {"model": ErrorRespuesta, "description": "DUPLICADO, YA_EXISTE_LIDER o UNICA_CUENTA_ACTIVA."},
    422: {"model": ErrorRespuesta, "description": "VALIDACION: datos inválidos."},
}


def get_cuentas_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    cognito: Annotated[CognitoAdmin, Depends(get_cognito_admin)],
) -> CuentasService:
    return CuentasService(session, cognito)


ServicioCuentas = Annotated[CuentasService, Depends(get_cuentas_service)]
SoloLider = Annotated[UsuarioActual, Depends(requiere_rol(Rol.LIDER_TECNICO))]
CualquierCuenta = Annotated[UsuarioActual, Depends(requiere_rol(Rol.LIDER_TECNICO, Rol.TECNICO))]


def a_cuenta_out(cuenta: CuentaFila) -> CuentaOut:
    return CuentaOut(
        id=cuenta.id,
        nombre=cuenta.nombre,
        correo=cuenta.correo,
        celular=a_celular_api_de(cuenta),
        rol=cuenta.rol,
        activo=cuenta.activo,
        casos_activos=cuenta.casos_activos,
        casos_pendientes=cuenta.casos_pendientes,
        unica_activa_del_rol=cuenta.unica_activa_del_rol,
    )


def a_carga_out(cuenta: CuentaFila) -> CargaTecnicoOut:
    return CargaTecnicoOut(
        id=cuenta.id,
        nombre=cuenta.nombre,
        rol=cuenta.rol,
        activos=cuenta.casos_activos,
        en_revision=cuenta.casos_pendientes,
    )


def a_celular_api_de(cuenta: CuentaFila) -> str:
    return a_celular_api(cuenta)
