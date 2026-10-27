from pydantic import BaseModel, Field

from app.core.roles import Rol


class UsuarioMe(BaseModel):
    """Cuenta de la sesión: es la fuente del rol en el panel."""

    id: str = Field(examples=["TEC-000001"])
    nombre: str
    correo: str
    celular: str = Field(pattern=r"^3\d{9}$", examples=["3001234567"])
    rol: Rol
    activo: bool
