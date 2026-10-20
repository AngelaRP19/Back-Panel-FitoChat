import hashlib
import hmac

SIGNATURE_PREFIX = "sha256="


def compute_signature(body: bytes, app_secret: str) -> str:
    """Firma en el mismo formato que envía Meta: 'sha256=<hex>'."""
    digest = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_PREFIX}{digest}"


def is_valid_signature(body: bytes, signature_header: str | None, app_secret: str) -> bool:
    """Valida X-Hub-Signature-256 sobre el cuerpo crudo, en tiempo constante."""
    if not signature_header or not signature_header.startswith(SIGNATURE_PREFIX):
        return False
    expected = compute_signature(body, app_secret)
    return hmac.compare_digest(expected.encode("utf-8"), signature_header.strip().encode("utf-8"))
