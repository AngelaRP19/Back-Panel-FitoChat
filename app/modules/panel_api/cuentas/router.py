from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.errors import ErrorRespuesta
from app.core.roles import Rol
from app.core.security_cognito import UsuarioActual, requiere_rol
from app.modules.panel_api.cuentas.schemas import UsuarioMe

router = APIRouter(tags=["cuentas"])

RESPUESTAS_AUTENTICACION: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorRespuesta, "description": "Sin token, vencido o inválido."},
    403: {"model": ErrorRespuesta, "description": "Cuenta inactiva o sin permiso."},
}


@router.get("/me", response_model=UsuarioMe, responses=RESPUESTAS_AUTENTICACION)
async def obtener_me(
    usuario: Annotated[UsuarioActual, Depends(requiere_rol(Rol.LIDER_TECNICO, Rol.TECNICO))],
) -> UsuarioMe:
    """Cuenta de la sesión actual. El frontend toma de aquí el rol."""
    return UsuarioMe(
        id=usuario.id,
        nombre=usuario.nombre,
        correo=usuario.correo,
        celular=usuario.celular,
        rol=usuario.rol,
        activo=usuario.activo,
    )
