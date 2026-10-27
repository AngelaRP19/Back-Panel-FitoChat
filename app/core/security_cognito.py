"""Validación del JWT de Cognito, usuario actual y roles (guía §7).

El backend solo VALIDA el access token que Cognito emitió al frontend: firma contra el JWKS del User Pool,
`iss`, `exp`, `token_use = access` y `client_id`. La cuenta y el rol salen de la base de datos (`cognito_sub`).
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any, Protocol

import jwt
from fastapi import Depends
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.core.config import get_settings
from app.core.roles import Rol
from app.core.telefonos import celular_de_e164
from app.core.tokens_prueba import obtener_claves_prueba
from app.infrastructure.db.cuentas_repository import buscar_cuenta_por_sub
from app.infrastructure.db.session import get_session

logger = logging.getLogger(__name__)

ALGORITMOS = ["RS256"]  # nunca se acepta el algoritmo que declare el token (evita 'none' y HS256)
_CABECERA_401 = {"WWW-Authenticate": "Bearer"}


class TokenInvalido(Exception):
    """El token no es válido (firma, emisor, tipo, cliente o formato)."""


class TokenVencido(TokenInvalido):
    """El token es válido pero ya venció."""


class JwksNoDisponible(Exception):
    """No se pudo consultar el JWKS: es un fallo del servidor, no del token."""


class ResolverClave(Protocol):
    def clave_para(self, token: str) -> Any: ...


class ClaveFija:
    """Clave pública fija (claves de prueba en `local` y `test`)."""

    def __init__(self, clave_publica: Any) -> None:
        self._clave = clave_publica

    def clave_para(self, token: str) -> Any:
        return self._clave


class ClaveDesdeJwks:
    """Clave pública según el `kid` del token, desde el JWKS del User Pool (caché con renovación)."""

    def __init__(self, jwks_url: str) -> None:
        # PyJWKClient guarda las claves una hora y vuelve a consultar el JWKS si llega un `kid` desconocido.
        self._cliente = PyJWKClient(jwks_url, cache_keys=True, lifespan=3600, timeout=5)

    def clave_para(self, token: str) -> Any:
        try:
            return self._cliente.get_signing_key_from_jwt(token).key
        except jwt.PyJWKClientConnectionError as exc:
            raise JwksNoDisponible from exc


@dataclass(frozen=True)
class ClaimsToken:
    sub: str
    client_id: str


class ValidadorToken:
    def __init__(self, *, issuer: str, client_id: str, resolver: ResolverClave) -> None:
        self._issuer = issuer
        self._client_id = client_id
        self._resolver = resolver

    def validar(self, token: str) -> ClaimsToken:
        try:
            clave = self._resolver.clave_para(token)
            payload = jwt.decode(
                token,
                clave,
                algorithms=ALGORITMOS,
                issuer=self._issuer,
                leeway=10,
                options={"require": ["exp", "iss", "sub"], "verify_aud": False},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenVencido from exc
        except jwt.PyJWTError as exc:
            raise TokenInvalido from exc
        # El access token de Cognito no trae `aud`: el cliente va en `client_id`. El ID token no se acepta.
        if payload.get("token_use") != "access":
            raise TokenInvalido
        if payload.get("client_id") != self._client_id:
            raise TokenInvalido
        sub = payload.get("sub")
        if not isinstance(sub, str) or not sub:
            raise TokenInvalido
        return ClaimsToken(sub=sub, client_id=self._client_id)


@lru_cache
def get_validador() -> ValidadorToken:
    settings = get_settings()
    resolver: ResolverClave
    if settings.usa_claves_de_prueba:
        resolver = ClaveFija(obtener_claves_prueba(settings).publica)
    else:
        resolver = ClaveDesdeJwks(settings.cognito_jwks_url)
    return ValidadorToken(issuer=settings.cognito_issuer, client_id=settings.cognito_client_id, resolver=resolver)


_bearer = HTTPBearer(auto_error=False)


def _no_autenticado(mensaje: str = "Tu sesión no es válida. Inicia sesión de nuevo.") -> ApiError:
    return ApiError(401, "NO_AUTENTICADO", mensaje, headers=_CABECERA_401)


async def claims_actuales(
    credenciales: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    validador: Annotated[ValidadorToken, Depends(get_validador)],
) -> ClaimsToken:
    if credenciales is None:
        raise _no_autenticado("Falta iniciar sesión.")
    try:
        return await run_in_threadpool(validador.validar, credenciales.credentials)
    except TokenVencido:
        raise ApiError(
            401, "TOKEN_VENCIDO", "Tu sesión venció. Inicia sesión de nuevo.", headers=_CABECERA_401
        ) from None
    except TokenInvalido:
        raise _no_autenticado() from None
    except JwksNoDisponible:
        logger.error("No se pudo consultar el JWKS de Cognito")
        raise ApiError(503, "ERROR_INTERNO", "No pudimos verificar tu sesión. Intenta de nuevo.") from None


async def token_actual(credenciales: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]) -> str:
    """El access token tal cual llegó (Cognito lo necesita para cambiar la contraseña). Va junto a `usuario_actual`."""
    if credenciales is None:
        raise _no_autenticado("Falta iniciar sesión.")
    return credenciales.credentials


@dataclass(frozen=True)
class UsuarioActual:
    id: str
    nombre: str
    correo: str
    celular: str  # 10 dígitos, como en la API
    rol: Rol
    activo: bool
    cognito_sub: str


async def usuario_actual(
    claims: Annotated[ClaimsToken, Depends(claims_actuales)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UsuarioActual:
    cuenta = await buscar_cuenta_por_sub(session, claims.sub)
    if cuenta is None:
        logger.warning("Token válido sin cuenta en el panel")
        raise ApiError(403, "SIN_PERMISO", "Tu cuenta no tiene acceso al panel.")
    if not cuenta.activo:
        raise ApiError(403, "CUENTA_INACTIVA", "Tu cuenta está deshabilitada. Habla con el líder técnico.")
    return UsuarioActual(
        id=cuenta.id,
        nombre=cuenta.nombre,
        correo=cuenta.correo,
        celular=celular_de_e164(cuenta.celular_e164),
        rol=cuenta.rol,
        activo=cuenta.activo,
        cognito_sub=claims.sub,
    )


def requiere_rol(*roles: Rol) -> Callable[..., Awaitable[UsuarioActual]]:
    """Dependencia que deja pasar solo a las cuentas con alguno de los roles indicados."""
    permitidos = frozenset(roles)

    async def dependencia(usuario: Annotated[UsuarioActual, Depends(usuario_actual)]) -> UsuarioActual:
        if usuario.rol not in permitidos:
            raise ApiError(403, "SIN_PERMISO", "No tienes permiso para esta acción.")
        return usuario

    return dependencia
