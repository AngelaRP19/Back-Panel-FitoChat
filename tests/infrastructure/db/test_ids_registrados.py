"""El generador de ids queda registrado en todos los procesos que usan los modelos, no solo en la API."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("modulo", ["app.workers.sqs_consumer", "app.jobs.run", "app.infrastructure.db.models"])
def test_cada_punto_de_entrada_registra_el_generador_de_ids(modulo: str) -> None:
    """En producción el consumidor creaba productores sin id (NotNullViolation): nunca importaba `ids`."""
    entorno = {**os.environ, "PYTHONPATH": str(RAIZ)}
    codigo = f"import sys, {modulo}; sys.exit(0 if 'app.infrastructure.db.ids' in sys.modules else 1)"
    assert subprocess.run([sys.executable, "-c", codigo], cwd=RAIZ, env=entorno, check=False).returncode == 0
