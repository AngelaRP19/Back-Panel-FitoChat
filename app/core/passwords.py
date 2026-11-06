"""Hash y verificación de contraseñas con Argon2id.

Las columnas contrasena_tecnico y contrasena_lider_tecnico guardan SOLO el hash que produce
hash_password(), nunca el texto plano. Nunca registres contraseñas (ni sus hashes) en los logs.

Nota: si la autenticación del panel se hace solo con Cognito (guía, P-09), estas columnas pueden
quedar sin usar. Como el modelo las define NOT NULL, en ese caso se guarda un hash de un valor
aleatorio (ver unusable_password_hash()).
"""

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # Argon2id con los parámetros recomendados por argon2-cffi


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """True si la contraseña corresponde al hash. Nunca lanza por hash inválido."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True si el hash se generó con parámetros más débiles que los actuales."""
    return _hasher.check_needs_rehash(password_hash)


def unusable_password_hash() -> str:
    """Hash de un secreto aleatorio que nadie conoce, para cuentas que solo entran por Cognito."""
    return _hasher.hash(secrets.token_urlsafe(32))
