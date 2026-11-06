"""Validación del JWT sin base de datos: el validador y el manejo del encabezado Authorization."""

import base64
import json
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import CLIENT_ID_PRUEBA, ISSUER_PRUEBA, get_settings
from app.core.security_cognito import (
    ClaveDesdeJwks,
    ClaveFija,
    JwksNoDisponible,
    TokenInvalido,
    TokenVencido,
    ValidadorToken,
)
from app.core.tokens_prueba import ClavesPrueba, emitir_token_de_prueba, obtener_claves_prueba

SUB = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(scope="module")
def claves() -> ClavesPrueba:
    return obtener_claves_prueba(get_settings())


@pytest.fixture(scope="module")
def validador(claves: ClavesPrueba) -> ValidadorToken:
    return ValidadorToken(issuer=ISSUER_PRUEBA, client_id=CLIENT_ID_PRUEBA, resolver=ClaveFija(claves.publica))


def _token(**kwargs: Any) -> str:
    return emitir_token_de_prueba(SUB, settings=get_settings(), **kwargs)


def test_token_valido(validador: ValidadorToken) -> None:
    claims = validador.validar(_token())
    assert claims.sub == SUB
    assert claims.client_id == CLIENT_ID_PRUEBA


def test_token_vencido(validador: ValidadorToken) -> None:
    with pytest.raises(TokenVencido):
        validador.validar(_token(ahora=time.time() - 7200, expira_en=3600))


def test_token_dentro_de_la_tolerancia_de_reloj(validador: ValidadorToken) -> None:
    """Venció hace 5 s: la tolerancia de 10 s lo acepta (relojes desfasados entre Cognito y ECS)."""
    validador.validar(_token(ahora=time.time() - 3605, expira_en=3600))


def test_firma_de_otra_clave(validador: ValidadorToken) -> None:
    ajena = ClavesPrueba(rsa.generate_private_key(public_exponent=65537, key_size=2048))
    with pytest.raises(TokenInvalido):
        validador.validar(_token(claves=ajena))


def test_client_id_incorrecto(validador: ValidadorToken) -> None:
    with pytest.raises(TokenInvalido):
        validador.validar(_token(client_id="otro-cliente"))


def test_id_token_no_se_acepta(validador: ValidadorToken) -> None:
    """El frontend debe enviar el access token, no el ID token."""
    with pytest.raises(TokenInvalido):
        validador.validar(_token(token_use="id"))


def test_emisor_incorrecto(validador: ValidadorToken) -> None:
    with pytest.raises(TokenInvalido):
        validador.validar(_token(issuer="https://cognito-idp.us-east-1.amazonaws.com/otro-pool"))


def test_sin_sub(validador: ValidadorToken, claves: ClavesPrueba) -> None:
    ahora = int(time.time())
    sin_sub = jwt.encode(
        {"iss": ISSUER_PRUEBA, "client_id": CLIENT_ID_PRUEBA, "token_use": "access", "exp": ahora + 60},
        claves.privada,
        algorithm="RS256",
    )
    with pytest.raises(TokenInvalido):
        validador.validar(sin_sub)


def test_sin_exp(validador: ValidadorToken, claves: ClavesPrueba) -> None:
    sin_exp = jwt.encode(
        {"sub": SUB, "iss": ISSUER_PRUEBA, "client_id": CLIENT_ID_PRUEBA, "token_use": "access"},
        claves.privada,
        algorithm="RS256",
    )
    with pytest.raises(TokenInvalido):
        validador.validar(sin_exp)


def _b64(datos: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(datos).encode()).rstrip(b"=").decode()


def test_algoritmo_none_se_rechaza(validador: ValidadorToken) -> None:
    ahora = int(time.time())
    sin_firma = (
        _b64({"alg": "none", "typ": "JWT"})
        + "."
        + _b64(
            {"sub": SUB, "iss": ISSUER_PRUEBA, "client_id": CLIENT_ID_PRUEBA, "token_use": "access", "exp": ahora + 60}
        )
        + "."
    )
    with pytest.raises(TokenInvalido):
        validador.validar(sin_firma)


def test_confusion_de_algoritmo_hs256_con_la_clave_publica(validador: ValidadorToken, claves: ClavesPrueba) -> None:
    """Ataque clásico: firmar con HS256 usando la clave pública como secreto compartido."""
    from cryptography.hazmat.primitives import serialization

    publica_pem = claves.publica.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    ahora = int(time.time())
    # PyJWT se niega a firmar HS256 con un PEM; se construye a mano para comprobar que el validador igual lo rechaza.
    import hashlib
    import hmac

    cabecera = _b64({"alg": "HS256", "typ": "JWT", "kid": "x"})
    cuerpo = _b64(
        {"sub": SUB, "iss": ISSUER_PRUEBA, "client_id": CLIENT_ID_PRUEBA, "token_use": "access", "exp": ahora + 60}
    )
    firma = (
        base64.urlsafe_b64encode(hmac.new(publica_pem, f"{cabecera}.{cuerpo}".encode(), hashlib.sha256).digest())
        .rstrip(b"=")
        .decode()
    )
    with pytest.raises(TokenInvalido):
        validador.validar(f"{cabecera}.{cuerpo}.{firma}")


@pytest.mark.parametrize("basura", ["", "abc", "a.b.c", "Bearer x", "....", "ñ"])
def test_tokens_mal_formados(validador: ValidadorToken, basura: str) -> None:
    with pytest.raises(TokenInvalido):
        validador.validar(basura)


def test_claves_de_prueba_no_existen_en_prod() -> None:
    from app.core.config import Settings

    prod = Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="prod",
        cognito_user_pool_id="p",
        cognito_app_client_id="c",
        s3_bucket_evidencias="b",
    )
    with pytest.raises(RuntimeError):
        obtener_claves_prueba(prod)


def test_jwks_caido_es_un_fallo_del_servidor(monkeypatch: pytest.MonkeyPatch) -> None:
    resolver = ClaveDesdeJwks("https://cognito-idp.invalid/pool/.well-known/jwks.json")

    def sin_red(_: str) -> Any:
        raise jwt.PyJWKClientConnectionError("sin red")

    monkeypatch.setattr(resolver._cliente, "get_signing_key_from_jwt", sin_red)
    with pytest.raises(JwksNoDisponible):
        resolver.clave_para("cualquiera")
    validador = ValidadorToken(issuer=ISSUER_PRUEBA, client_id=CLIENT_ID_PRUEBA, resolver=resolver)
    with pytest.raises(JwksNoDisponible):  # no se confunde con un token inválido
        validador.validar("a.b.c")
