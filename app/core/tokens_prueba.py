"""Claves y tokens de PRUEBA para `local` y `test`. En `prod` este módulo nunca se usa para validar.

- `test`: el par de claves se genera en memoria al arrancar el proceso.
- `local`: el par se guarda en `JWT_PRUEBA_KEY_PATH` (fuera del repositorio) para que lo compartan la API y
  el comando que emite tokens:  python -m app.core.tokens_prueba <sub> [--rol ...]

Con `COGNITO_USER_POOL_ID` y `COGNITO_APP_CLIENT_ID` definidos, `local` valida contra el User Pool real y estas
claves no se usan.
"""

import argparse
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import Settings, get_settings

KID_PRUEBA = "clave-de-prueba"


@dataclass(frozen=True)
class ClavesPrueba:
    privada: rsa.RSAPrivateKey

    @property
    def publica(self) -> rsa.RSAPublicKey:
        return self.privada.public_key()


def _generar() -> ClavesPrueba:
    return ClavesPrueba(rsa.generate_private_key(public_exponent=65537, key_size=2048))


def _cargar_o_crear(ruta: Path) -> ClavesPrueba:
    if ruta.exists():
        try:
            privada = serialization.load_pem_private_key(ruta.read_bytes(), password=None)
        except ValueError:
            privada = None  # archivo dañado: se genera un par nuevo
        if isinstance(privada, rsa.RSAPrivateKey):
            return ClavesPrueba(privada)
    claves = _generar()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(
        claves.privada.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return claves


@lru_cache
def _claves_en_memoria() -> ClavesPrueba:
    return _generar()


def obtener_claves_prueba(settings: Settings | None = None) -> ClavesPrueba:
    settings = settings or get_settings()
    if settings.en_prod:
        raise RuntimeError("Las claves de prueba no existen en prod")
    if settings.app_env == "local":
        try:
            return _cargar_o_crear(Path(settings.jwt_prueba_key_path))
        except OSError:
            pass  # sin permiso de escritura: se usa una clave en memoria (válida solo para este proceso)
    return _claves_en_memoria()


def emitir_token_de_prueba(
    sub: str,
    *,
    settings: Settings | None = None,
    claves: ClavesPrueba | None = None,
    token_use: str = "access",
    client_id: str | None = None,
    issuer: str | None = None,
    expira_en: int = 3600,
    ahora: float | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Emite un access token con la forma del de Cognito (los argumentos permiten fabricar tokens inválidos)."""
    settings = settings or get_settings()
    claves = claves or obtener_claves_prueba(settings)
    emitido = int(ahora if ahora is not None else time.time())
    payload: dict[str, Any] = {
        "sub": sub,
        "iss": issuer or settings.cognito_issuer,
        "client_id": client_id or settings.cognito_client_id,
        "token_use": token_use,
        "scope": "aws.cognito.signin.user.admin",
        "iat": emitido,
        "exp": emitido + expira_en,
        "jti": f"prueba-{emitido}",
        **(extra or {}),
    }
    return jwt.encode(payload, claves.privada, algorithm="RS256", headers={"kid": KID_PRUEBA})


def main() -> None:
    parser = argparse.ArgumentParser(description="Emite un access token de prueba (solo local y test)")
    parser.add_argument("sub", help="cognito_sub de la cuenta (columna cognito_sub de tecnicos o lideres_tecnicos)")
    parser.add_argument("--horas", type=float, default=8, help="vigencia en horas (por defecto 8)")
    args = parser.parse_args()
    settings = get_settings()
    if not settings.usa_claves_de_prueba:
        raise SystemExit("Este entorno valida contra Cognito real: usa un token de Cognito")
    print(emitir_token_de_prueba(args.sub, settings=settings, expira_en=int(args.horas * 3600)))


if __name__ == "__main__":
    main()
