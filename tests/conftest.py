import asyncio
import os
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import date
from typing import Any

# El driver asíncrono de psycopg no funciona con el ProactorEventLoop de Windows (guía §3).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Variables antes de importar la app (Settings se valida al crearla).
os.environ.update(
    {
        "APP_ENV": "test",
        "AWS_REGION": "us-east-1",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "SQS_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123456789012/sin-usar.fifo",
        "WHATSAPP_VERIFY_TOKEN": "token-de-prueba",
        "WHATSAPP_APP_SECRET": "secreto-de-prueba",
        "LOG_LEVEL": "DEBUG",
    }
)
for variable in (
    "AWS_ENDPOINT_URL",
    "AWS_EXECUTION_ENV",
    "COGNITO_USER_POOL_ID",
    "COGNITO_APP_CLIENT_ID",
    "COGNITO_REGION",
    "CORS_ORIGINS",
    "S3_BUCKET_EVIDENCIAS",
    "DB_HOST",
    "DB_USER",
    "DB_PASSWORD",
    "DB_SSLMODE",
):
    os.environ.pop(variable, None)
# Base aparte para los tests (la crea tests/db_fixtures.py); nunca la de desarrollo.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://anuc:anuc_local@localhost:5432/anuc_test"
)
os.environ.setdefault("EMBEDDING_DIM", "1024")

import boto3  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from moto import mock_aws  # noqa: E402
from sqlalchemy import Engine, delete  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.config import get_database_settings, get_settings  # noqa: E402
from app.core.roles import Rol  # noqa: E402
from app.core.tokens_prueba import emitir_token_de_prueba  # noqa: E402
from app.infrastructure.db.models import LiderTecnico, Tecnico  # noqa: E402
from app.infrastructure.db.session import get_session  # noqa: E402
from app.infrastructure.sqs_queue_client import SqsQueueClient, get_sqs_queue_client  # noqa: E402
from app.main import app  # noqa: E402
from tests.db_fixtures import conn, engine, migrated, session  # noqa: E402, F401  (fixtures compartidas)

APP_SECRET = "secreto-de-prueba"
VERIFY_TOKEN = "token-de-prueba"
QUEUE_NAME = "mensajes-entrantes.fifo"


@pytest.fixture
def sqs() -> Iterator[tuple[Any, str]]:
    """Cola FIFO simulada con moto."""
    with mock_aws():
        client = boto3.client("sqs", region_name="us-east-1")
        queue_url = client.create_queue(
            QueueName=QUEUE_NAME,
            Attributes={"FifoQueue": "true", "ContentBasedDeduplication": "false"},
        )["QueueUrl"]
        yield client, queue_url


@pytest.fixture
def client(sqs: tuple[Any, str]) -> Iterator[TestClient]:
    sqs_client, queue_url = sqs
    app.dependency_overrides[get_sqs_queue_client] = lambda: SqsQueueClient(sqs_client, queue_url)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# --- API del panel (necesita Postgres) ---------------------------------------------------------------------


async def _sesion_por_solicitud() -> AsyncIterator[AsyncSession]:
    """Sesión nueva por solicitud, sin pool: cada TestClient corre en su propio event loop."""
    motor = create_async_engine(get_database_settings().sqlalchemy_url, poolclass=NullPool)
    try:
        async with AsyncSession(motor, expire_on_commit=False) as sesion:
            yield sesion
    finally:
        await motor.dispose()


@pytest.fixture
def cliente_panel(migrated: Engine) -> Iterator[TestClient]:  # noqa: F811
    app.dependency_overrides[get_session] = _sesion_por_solicitud
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


CrearCuenta = Callable[..., str]


@pytest.fixture
def crear_cuenta(migrated: Engine) -> Iterator[CrearCuenta]:  # noqa: F811
    """Crea una cuenta con COMMIT (la API usa otra conexión) y la borra al terminar el test."""
    creadas: list[tuple[type[Tecnico] | type[LiderTecnico], str]] = []

    def crear(rol: Rol, *, sub: str, activo: bool = True, numero: int = 1) -> str:
        celular = f"57300000{numero:04d}"
        estado = "ACTIVO" if activo else "DESHABILITADO"
        with Session(migrated) as sesion:
            if rol is Rol.TECNICO:
                fila: Tecnico | LiderTecnico = Tecnico(
                    id_tecnico=f"TEC-9{numero:05d}",
                    nombre_tecnico="Ana Técnica",
                    correo_tecnico=f"ana{numero}@demo.test",
                    celular_tecnico=celular,
                    contrasena_tecnico="hash-inutilizable",
                    estado_cuenta_tecnico=estado,
                    fecha_registro_tecnico=date(2026, 10, 1),
                    cognito_sub=sub,
                )
                creadas.append((Tecnico, fila.id_tecnico))
            else:
                fila = LiderTecnico(
                    id_lider_tecnico=f"LID-9{numero:05d}",
                    nombre_lider_tecnico="Luis Líder",
                    correo_lider_tecnico=f"luis{numero}@demo.test",
                    celular_lider_tecnico=celular,
                    contrasena_lider_tecnico="hash-inutilizable",
                    estado_cuenta_lider_tecnico=estado,
                    cognito_sub=sub,
                )
                creadas.append((LiderTecnico, fila.id_lider_tecnico))
            sesion.add(fila)
            sesion.commit()
            return fila.id_tecnico if isinstance(fila, Tecnico) else fila.id_lider_tecnico

    yield crear

    with Session(migrated) as sesion:
        for modelo, id_fila in creadas:
            columna = Tecnico.id_tecnico if modelo is Tecnico else LiderTecnico.id_lider_tecnico
            sesion.execute(delete(modelo).where(columna == id_fila))
        sesion.commit()


def token_de(sub: str, **kwargs: Any) -> str:
    """Access token de prueba con la forma del de Cognito, firmado con la clave de pruebas."""
    return emitir_token_de_prueba(sub, settings=get_settings(), **kwargs)


def con_token(sub: str, **kwargs: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_de(sub, **kwargs)}"}
