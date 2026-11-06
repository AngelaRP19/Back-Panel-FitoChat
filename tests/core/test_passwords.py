from app.core.passwords import hash_password, needs_rehash, unusable_password_hash, verify_password


def test_hash_no_es_texto_plano_y_verifica() -> None:
    hashed = hash_password("Clave123")
    assert hashed != "Clave123"
    assert hashed.startswith("$argon2id$")
    assert len(hashed) <= 255  # cabe en contrasena_*
    assert verify_password("Clave123", hashed)


def test_contrasena_incorrecta() -> None:
    assert not verify_password("otra", hash_password("Clave123"))


def test_hash_invalido_no_lanza() -> None:
    assert not verify_password("Clave123", "no-es-un-hash")


def test_mismo_texto_produce_hashes_distintos() -> None:
    assert hash_password("Clave123") != hash_password("Clave123")


def test_hash_inutilizable() -> None:
    hashed = unusable_password_hash()
    assert not verify_password("", hashed)
    assert not needs_rehash(hashed)
