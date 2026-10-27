from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.core.security_cognito import UsuarioActual, token_actual
from app.modules.panel_api.cuentas.dependencias import (
    RESPUESTAS_AUTENTICACION,
    RESPUESTAS_ESCRITURA,
    CualquierCuenta,
    ServicioCuentas,
)
from app.modules.panel_api.cuentas.repository import CuentaFila
from app.modules.panel_api.cuentas.schemas import ActualizarMe, CambiarContrasenaIn, UsuarioMe
from app.modules.panel_api.cuentas.service import a_celular_api

router = APIRouter(tags=["cuentas"])


def _usuario_me(cuenta: CuentaFila | UsuarioActual) -> UsuarioMe:
    celular = a_celular_api(cuenta) if isinstance(cuenta, CuentaFila) else cuenta.celular
    return UsuarioMe(
        id=cuenta.id, nombre=cuenta.nombre, correo=cuenta.correo, celular=celular, rol=cuenta.rol, activo=cuenta.activo
    )


@router.get("/me", response_model=UsuarioMe, responses=RESPUESTAS_AUTENTICACION)
async def obtener_me(usuario: CualquierCuenta) -> UsuarioMe:
    """Cuenta de la sesión actual. El frontend toma de aquí el rol."""
    return _usuario_me(usuario)


@router.patch("/me", response_model=UsuarioMe, responses=RESPUESTAS_ESCRITURA)
async def actualizar_me(datos: ActualizarMe, usuario: CualquierCuenta, servicio: ServicioCuentas) -> UsuarioMe:
    """Mi cuenta (RQF04): cada persona edita solo su nombre y su celular. El correo no se edita."""
    return _usuario_me(await servicio.actualizar_mi_cuenta(usuario, datos))


@router.post("/me/contrasena", status_code=status.HTTP_204_NO_CONTENT, responses=RESPUESTAS_ESCRITURA)
async def cambiar_mi_contrasena(
    datos: CambiarContrasenaIn,
    usuario: CualquierCuenta,
    token: Annotated[str, Depends(token_actual)],
    servicio: ServicioCuentas,
) -> Response:
    """Cambia la contraseña en Cognito. La actual se verifica allí: el backend nunca la guarda."""
    await servicio.cambiar_contrasena(usuario, token, datos)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
