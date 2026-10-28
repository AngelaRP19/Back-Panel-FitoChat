"""Administración de cuentas en Cognito (guía §12.3): alta, baja lógica, atributos y cambio de contraseña.

Las contraseñas de las cuentas de técnicos y líderes viven SOLO en Cognito. El backend nunca las guarda ni las registra:
a un técnico nuevo Cognito le envía una contraseña temporal por correo (reto `NEW_PASSWORD_REQUIRED` en el primer
ingreso, que el frontend ya maneja) y el primer líder define la suya al crear la cuenta.

- `CognitoReal`: boto3 contra el User Pool (`COGNITO_USER_POOL_ID`).
- `CognitoSimulado`: en memoria, para `local`/`test` sin User Pool. No sale a internet.

Ambos tienen la misma interfaz (`CognitoAdmin`). Las llamadas son síncronas: la capa de servicio las ejecuta en un hilo.
"""

import logging
import threading
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class ErrorCognito(Exception):
    """Fallo de Cognito que no es un error del usuario (red, permisos, límites)."""


class UsuarioYaExiste(ErrorCognito):
    """Ya hay un usuario con ese correo en el User Pool."""


class UsuarioNoExiste(ErrorCognito):
    pass


class ContrasenaNoValida(ErrorCognito):
    """La contraseña no cumple la política del User Pool."""


class ContrasenaActualIncorrecta(ErrorCognito):
    pass


class CognitoAdmin(Protocol):
    def crear_usuario(self, correo: str, nombre: str, celular_e164: str, contrasena: str | None = None) -> str:
        """Crea el usuario y devuelve su `sub`. Sin contraseña: Cognito envía una temporal por correo."""

    def eliminar_usuario(self, correo: str) -> None: ...

    def deshabilitar(self, correo: str) -> None: ...

    def habilitar(self, correo: str) -> None: ...

    def actualizar_atributos(
        self,
        correo: str,
        *,
        nombre: str | None = None,
        celular_e164: str | None = None,
        correo_nuevo: str | None = None,
    ) -> None: ...

    def cambiar_contrasena(self, access_token: str, actual: str, nueva: str) -> None: ...


def _a_cognito_telefono(celular_e164: str) -> str:
    return celular_e164 if celular_e164.startswith("+") else f"+{celular_e164}"


class CognitoReal:
    def __init__(self, client: Any, user_pool_id: str) -> None:
        self._client = client
        self._pool = user_pool_id

    def _llamar(self, operacion: str, **kwargs: Any) -> Any:
        try:
            return getattr(self._client, operacion)(**kwargs)
        except ClientError as error:
            codigo = error.response["Error"]["Code"]
            if codigo == "UsernameExistsException":
                raise UsuarioYaExiste from error
            if codigo == "UserNotFoundException":
                raise UsuarioNoExiste from error
            if codigo == "InvalidPasswordException":
                raise ContrasenaNoValida from error
            if codigo == "NotAuthorizedException" and operacion == "change_password":
                raise ContrasenaActualIncorrecta from error
            raise ErrorCognito(codigo) from error

    def crear_usuario(self, correo: str, nombre: str, celular_e164: str, contrasena: str | None = None) -> str:
        atributos = [
            {"Name": "email", "Value": correo},
            {"Name": "email_verified", "Value": "true"},
            {"Name": "name", "Value": nombre},
            {"Name": "phone_number", "Value": _a_cognito_telefono(celular_e164)},
        ]
        argumentos: dict[str, Any] = {"UserPoolId": self._pool, "Username": correo, "UserAttributes": atributos}
        if contrasena is None:
            argumentos["DesiredDeliveryMediums"] = ["EMAIL"]  # Cognito envía la contraseña temporal
        else:
            argumentos["MessageAction"] = "SUPPRESS"
        respuesta = self._llamar("admin_create_user", **argumentos)
        if contrasena is not None:
            try:
                self._llamar(
                    "admin_set_user_password",
                    UserPoolId=self._pool,
                    Username=correo,
                    Password=contrasena,
                    Permanent=True,
                )
            except ErrorCognito:
                try:
                    self._llamar("admin_delete_user", UserPoolId=self._pool, Username=correo)  # no dejarlo a medias
                except ErrorCognito:
                    # Se informa el error original (p. ej., contraseña que no cumple la política), no el de borrar.
                    logger.exception("No se pudo borrar el usuario a medio crear en Cognito; revisarlo a mano")
                raise
        sub = next(a["Value"] for a in respuesta["User"]["Attributes"] if a["Name"] == "sub")
        return str(sub)

    def eliminar_usuario(self, correo: str) -> None:
        self._llamar("admin_delete_user", UserPoolId=self._pool, Username=correo)

    def deshabilitar(self, correo: str) -> None:
        self._llamar("admin_disable_user", UserPoolId=self._pool, Username=correo)

    def habilitar(self, correo: str) -> None:
        self._llamar("admin_enable_user", UserPoolId=self._pool, Username=correo)

    def actualizar_atributos(
        self,
        correo: str,
        *,
        nombre: str | None = None,
        celular_e164: str | None = None,
        correo_nuevo: str | None = None,
    ) -> None:
        atributos: list[dict[str, str]] = []
        if nombre is not None:
            atributos.append({"Name": "name", "Value": nombre})
        if celular_e164 is not None:
            atributos.append({"Name": "phone_number", "Value": _a_cognito_telefono(celular_e164)})
        if correo_nuevo is not None:
            atributos += [{"Name": "email", "Value": correo_nuevo}, {"Name": "email_verified", "Value": "true"}]
        if atributos:
            self._llamar(
                "admin_update_user_attributes", UserPoolId=self._pool, Username=correo, UserAttributes=atributos
            )

    def cambiar_contrasena(self, access_token: str, actual: str, nueva: str) -> None:
        self._llamar("change_password", PreviousPassword=actual, ProposedPassword=nueva, AccessToken=access_token)


@dataclass
class _UsuarioSimulado:
    sub: str
    correo: str
    nombre: str
    celular: str
    habilitado: bool = True
    contrasena: str | None = None


class CognitoSimulado:
    """User Pool en memoria. La contraseña de una cuenta nueva sin contraseña es «temporal» (no se emite ninguna)."""

    def __init__(self) -> None:
        self._por_correo: dict[str, _UsuarioSimulado] = {}
        self._lock = threading.Lock()
        self.eliminados: list[str] = []

    def _usuario(self, correo: str) -> _UsuarioSimulado:
        try:
            return self._por_correo[correo.lower()]
        except KeyError:
            raise UsuarioNoExiste from None

    def crear_usuario(self, correo: str, nombre: str, celular_e164: str, contrasena: str | None = None) -> str:
        with self._lock:
            if correo.lower() in self._por_correo:
                raise UsuarioYaExiste
            usuario = _UsuarioSimulado(str(uuid.uuid4()), correo, nombre, celular_e164, contrasena=contrasena)
            self._por_correo[correo.lower()] = usuario
            return usuario.sub

    def eliminar_usuario(self, correo: str) -> None:
        with self._lock:
            self._usuario(correo)
            del self._por_correo[correo.lower()]
            self.eliminados.append(correo.lower())

    def deshabilitar(self, correo: str) -> None:
        self._usuario(correo).habilitado = False

    def habilitar(self, correo: str) -> None:
        self._usuario(correo).habilitado = True

    def actualizar_atributos(
        self,
        correo: str,
        *,
        nombre: str | None = None,
        celular_e164: str | None = None,
        correo_nuevo: str | None = None,
    ) -> None:
        with self._lock:
            usuario = self._usuario(correo)
            if correo_nuevo is not None and correo_nuevo.lower() != correo.lower():
                if correo_nuevo.lower() in self._por_correo:
                    raise UsuarioYaExiste
                del self._por_correo[correo.lower()]
                usuario.correo = correo_nuevo
                self._por_correo[correo_nuevo.lower()] = usuario
            if nombre is not None:
                usuario.nombre = nombre
            if celular_e164 is not None:
                usuario.celular = celular_e164

    def cambiar_contrasena(self, access_token: str, actual: str, nueva: str) -> None:
        """El token de prueba lleva el `sub`: se busca el usuario por él."""
        import jwt

        sub = jwt.decode(access_token, options={"verify_signature": False}).get("sub")
        usuario = next((u for u in self._por_correo.values() if u.sub == sub), None)
        if usuario is None:
            raise UsuarioNoExiste
        if usuario.contrasena is not None and usuario.contrasena != actual:
            raise ContrasenaActualIncorrecta
        if len(nueva) < 8:
            raise ContrasenaNoValida
        usuario.contrasena = nueva

    # --- ayudas para las pruebas ---------------------------------------------------------------------------------
    def existe(self, correo: str) -> bool:
        return correo.lower() in self._por_correo

    def obtener(self, correo: str) -> _UsuarioSimulado:
        return self._usuario(correo)


_SIMULADO = CognitoSimulado()


@lru_cache
def _cognito_real() -> CognitoReal:
    settings = get_settings()
    cliente = boto3.client(
        "cognito-idp",
        region_name=settings.cognito_region or settings.aws_region,
        config=Config(retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=3, read_timeout=10),
    )
    return CognitoReal(cliente, settings.cognito_user_pool_id or "")


def get_cognito_admin() -> CognitoAdmin:
    """Con claves de prueba (`local` sin User Pool y `test`) se usa el simulado; con User Pool, el real."""
    return _SIMULADO if get_settings().usa_claves_de_prueba else _cognito_real()
