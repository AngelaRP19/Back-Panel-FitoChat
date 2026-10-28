import re
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from app.core.roles import Rol
from app.core.telefonos import PATRON_CELULAR, normalizar_celular

_CORREO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _nombre(valor: str) -> str:
    valor = valor.strip()
    if not valor:
        raise ValueError("Escribe el nombre completo.")
    if len(valor) > 100:
        raise ValueError("El nombre no puede pasar de 100 caracteres.")
    return valor


def _correo(valor: str) -> str:
    valor = valor.strip().lower()
    if len(valor) > 100 or not _CORREO.fullmatch(valor):
        raise ValueError("Escribe un correo válido, por ejemplo nombre@anuc.org")
    return valor


def _celular(valor: str) -> str:
    valor = normalizar_celular(valor)  # el panel envía E.164 (+573001234567)
    if not PATRON_CELULAR.fullmatch(valor):
        raise ValueError("Escribe un celular de 10 dígitos que empiece por 3")
    return valor


def _contrasena(valor: str) -> str:
    """Política del User Pool: mínimo 8 caracteres, con letras y números."""
    if len(valor) < 8 or not re.search(r"[A-Za-z]", valor) or not re.search(r"\d", valor):
        raise ValueError("Mínimo 8 caracteres, con letras y números.")
    if len(valor) > 128:
        raise ValueError("La contraseña no puede pasar de 128 caracteres.")
    return valor


Nombre = Annotated[str, AfterValidator(_nombre)]
Correo = Annotated[str, AfterValidator(_correo)]
Celular = Annotated[str, AfterValidator(_celular), Field(examples=["3001234567"])]
Contrasena = Annotated[str, AfterValidator(_contrasena)]


# --- Autenticación -------------------------------------------------------------------------------------------------


class EstadoAuth(BaseModel):
    hay_lider: bool


class PrimerLiderIn(BaseModel):
    nombre: Nombre
    correo: Correo
    celular: Celular
    password: Contrasena


class PrimerLiderOut(BaseModel):
    id: str


class VerificarCorreoIn(BaseModel):
    correo: Correo


class VerificarCorreoOut(BaseModel):
    existe: bool


# --- Mi cuenta -----------------------------------------------------------------------------------------------------


class UsuarioMe(BaseModel):
    """Cuenta de la sesión: es la fuente del rol en el panel."""

    id: str = Field(examples=["TEC-000001"])
    nombre: str
    correo: str
    celular: str = Field(pattern=r"^3\d{9}$", examples=["3001234567"])
    rol: Rol
    activo: bool


class ActualizarMe(BaseModel):
    """`PATCH /me`: solo nombre y celular. El correo es el usuario de Cognito y no se edita."""

    nombre: Nombre | None = None
    celular: Celular | None = None
    correo: str | None = Field(default=None, exclude=True)

    @field_validator("correo")
    @classmethod
    def _correo_no_se_edita(cls, valor: str | None) -> None:
        if valor is not None:
            raise ValueError("El correo no se puede cambiar.")


class CambiarContrasenaIn(BaseModel):
    actual: str = Field(min_length=1, max_length=128)
    nueva: Contrasena


# --- Cuentas (técnicos y líderes) ------------------------------------------------------------------------------------


class CuentaIn(BaseModel):
    nombre: Nombre
    correo: Correo
    celular: Celular
    rol: Rol


class CuentaEditar(BaseModel):
    """Todo es opcional. El rol no se puede cambiar: cada rol tiene su tabla y su usuario de Cognito."""

    model_config = ConfigDict(extra="ignore")

    nombre: Nombre | None = None
    correo: Correo | None = None
    celular: Celular | None = None
    rol: Rol | None = None


class CambiarEstadoIn(BaseModel):
    activo: bool


class CuentaOut(BaseModel):
    id: str
    nombre: str
    correo: str
    celular: str = Field(pattern=r"^3\d{9}$")
    rol: Rol
    activo: bool
    casos_activos: int = Field(description="Casos En revisión y Registrados sin cerrar")
    casos_pendientes: int = Field(description="Casos En revisión (sin diagnóstico)")
    unica_activa_del_rol: bool = Field(description="Es la única cuenta activa de su rol: no se puede deshabilitar")


class CargaTecnicoOut(BaseModel):
    """Técnico activo con su carga, para elegir a quién asignar un caso."""

    id: str
    nombre: str
    rol: Rol
    activos: int
    en_revision: int
