import hashlib
import hmac

from app.modules.webhook.signature import compute_signature, is_valid_signature

SECRET = "s3cr3t"
BODY = b'{"a":1}'


def test_compute_signature_formato_meta() -> None:
    expected = hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()
    assert compute_signature(BODY, SECRET) == f"sha256={expected}"


def test_firma_valida() -> None:
    assert is_valid_signature(BODY, compute_signature(BODY, SECRET), SECRET)


def test_firma_invalida() -> None:
    assert not is_valid_signature(BODY, compute_signature(b'{"a":2}', SECRET), SECRET)


def test_cabecera_ausente_o_sin_prefijo() -> None:
    assert not is_valid_signature(BODY, None, SECRET)
    assert not is_valid_signature(BODY, "", SECRET)
    assert not is_valid_signature(BODY, compute_signature(BODY, SECRET).removeprefix("sha256="), SECRET)
