"""Fixtures contra el Postgres del docker-compose (PostGIS + pgvector). Las importa tests/conftest.py.

Usan la base anuc_test (se crea si no existe). Si el Postgres no está levantado, estos tests se
omiten en lugar de fallar, para que el resto de la suite corra sin Docker.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import get_database_settings
from app.infrastructure.db.ids import PREFIJOS_ID, nombre_secuencia

ROOT = Path(__file__).resolve().parents[1]


def alembic_config(connection: Connection) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.attributes["connection"] = connection
    config.attributes["configure_logger"] = False
    return config


def _ensure_database(url: str) -> None:
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": target.database})
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    finally:
        admin.dispose()


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    url = get_database_settings().sqlalchemy_url
    try:
        _ensure_database(url)
    except OperationalError as exc:
        pytest.skip(f"Postgres no disponible ({exc.__class__.__name__}); levanta `docker compose up -d db`")
    eng = create_engine(url)
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def migrated(engine: Engine) -> Engine:
    """Base en `head` (parte de cero para no depender de ejecuciones anteriores)."""
    with engine.begin() as conn:
        command.downgrade(alembic_config(conn), "base")
        command.upgrade(alembic_config(conn), "head")
    return engine


def _reiniciar_secuencias_cortas(connection: Connection) -> None:
    """Las secuencias de catálogo tienen 3 dígitos (ES001..ES999) y nextval no se revierte con el rollback: tras unos
    cientos de pruebas se agotarían. ALTER SEQUENCE ... RESTART sí es transaccional, así que cada prueba arranca en 1
    y al revertirse la transacción la secuencia vuelve a su valor."""
    for tabla, spec in PREFIJOS_ID.items():
        if spec.digitos <= 3:
            connection.execute(text(f"ALTER SEQUENCE {nombre_secuencia(tabla)} RESTART WITH 1"))


@pytest.fixture
def conn(migrated: Engine) -> Iterator[Connection]:
    """Conexión con transacción que se revierte al final: cada test deja la base limpia."""
    with migrated.connect() as connection:
        transaction = connection.begin()
        _reiniciar_secuencias_cortas(connection)
        yield connection
        transaction.rollback()


@pytest.fixture
def session(conn: Connection) -> Iterator[Session]:
    with Session(bind=conn, join_transaction_mode="create_savepoint") as s:
        yield s
