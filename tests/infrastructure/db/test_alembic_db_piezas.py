"""Aceptación del 0b: `alembic upgrade head` funciona con DB_* (con DB_SSLMODE=disable en local)."""

import os
import subprocess
import sys

import pytest
from sqlalchemy import Engine, create_engine, inspect, make_url, text

from app.core.config import get_database_settings
from tests.db_fixtures import ROOT

BASE_TEMPORAL = "anuc_test_db_piezas"


def test_upgrade_head_con_variables_db_por_piezas(migrated: Engine) -> None:
    destino = make_url(get_database_settings().sqlalchemy_url)
    admin = create_engine(destino.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{BASE_TEMPORAL}"'))
            conn.execute(text(f'CREATE DATABASE "{BASE_TEMPORAL}"'))

        entorno = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
        entorno.update(
            DB_HOST=str(destino.host),
            DB_PORT=str(destino.port),
            DB_NAME=BASE_TEMPORAL,
            DB_USER=str(destino.username),
            DB_PASSWORD=str(destino.password),
            DB_SSLMODE="disable",
        )
        resultado = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env=entorno,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert resultado.returncode == 0, resultado.stderr[-2000:]

        motor = create_engine(destino.set(database=BASE_TEMPORAL))
        try:
            assert {"casos", "fincas", "tecnicos"} <= set(inspect(motor).get_table_names())
        finally:
            motor.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{BASE_TEMPORAL}" WITH (FORCE)'))
        admin.dispose()


def test_sin_conexion_alembic_falla_con_mensaje_claro() -> None:
    entorno = {k: v for k, v in os.environ.items() if k not in {"DATABASE_URL", "DB_HOST", "DB_USER", "DB_PASSWORD"}}
    resultado = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=ROOT,
        env=entorno,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert resultado.returncode != 0
    assert "DATABASE_URL" in resultado.stderr
    pytest.importorskip("sqlalchemy")
