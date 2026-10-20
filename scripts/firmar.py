"""Genera la cabecera X-Hub-Signature-256 para un archivo, igual que Meta.

Uso:  python scripts/firmar.py scripts/ejemplos/imagen.json [secreto]
El secreto por defecto es el de docker-compose (secreto-local).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.modules.webhook.signature import compute_signature  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    body = Path(sys.argv[1]).read_bytes()  # bytes exactos: los mismos que enviará curl --data-binary
    secret = sys.argv[2] if len(sys.argv) > 2 else "secreto-local"
    print(compute_signature(body, secret))


if __name__ == "__main__":
    main()
