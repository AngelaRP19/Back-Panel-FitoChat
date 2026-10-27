"""Fixtures de los jobs: base confirmada de verdad (los jobs abren sus propias sesiones), S3 con versionado y reloj fijo."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import boto3
import pytest
from botocore.config import Config
from moto import mock_aws
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.infrastructure.db.models import Cultivo, CultivoEspecie, Finca, Productor
from app.infrastructure.db.seed import seed
from app.infrastructure.s3_storage_client import S3StorageClient
from app.infrastructure.whatsapp_client import WhatsAppSimulado
from app.modules.scheduler.jobs import Contexto
from tests.api.conftest import vaciar

BUCKET = "anuc-evidencias-test"
AHORA = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)  # 10:00 en Bogotá


@dataclass
class Entorno:
    """Un productor con una finca y un cultivo de gulupa, sobre catálogos reales."""

    motor: Engine
    id_productor: str
    celular_e164: str
    id_cultivo: str


@pytest.fixture
def entorno(migrated: Engine) -> Iterator[Entorno]:
    vaciar(migrated)
    with migrated.begin() as conexion:
        seed(conexion)
    with Session(migrated) as sesion:
        productor = Productor(
            nombre_productor="Ana Pérez",
            telefono_productor="573001112233",
            estado_cuenta_productor="ACTIVO",
            fecha_registro_productor=date(2026, 1, 5),
        )
        sesion.add(productor)
        sesion.flush()
        finca = Finca(
            nombre_finca="La Esperanza",
            vereda_sector_finca="Centro",
            latitud_finca=5.25,
            longitud_finca=-73.17,
            id_productor=productor.id_productor,
        )
        sesion.add(finca)
        sesion.flush()
        cultivo = Cultivo(
            nombre_cultivo="Lote 1",
            latitud_cultivo=5.25,
            longitud_cultivo=-73.17,
            id_finca=finca.id_finca,
            tipo_cultivo="CAMPO_ABIERTO",
        )
        sesion.add(cultivo)
        sesion.flush()
        id_especie = sesion.scalar(text("SELECT id_especie FROM especies WHERE nombre_especie = 'Gulupa'"))
        sesion.add(CultivoEspecie(id_cultivo=cultivo.id_cultivo, id_especie=id_especie))
        sesion.commit()
        entorno = Entorno(migrated, productor.id_productor, "573001112233", cultivo.id_cultivo)
    yield entorno
    vaciar(migrated)


@dataclass
class AlmacenS3:
    cliente: S3StorageClient
    crudo: Any

    def subir(self, clave: str, versiones: int = 2) -> None:
        """Sube el objeto varias veces: el bucket versionado conserva cada una."""
        for n in range(versiones):
            self.cliente.subir(clave, f"contenido {n}".encode())

    def borrar_simple(self, clave: str) -> None:
        """Un DeleteObject sin versión: solo agrega un marcador de borrado (la foto sigue recuperable)."""
        self.crudo.delete_object(Bucket=BUCKET, Key=clave)

    def versiones(self, clave: str) -> int:
        return len(self.cliente.listar_versiones(clave))


@pytest.fixture
def s3() -> Iterator[AlmacenS3]:
    with mock_aws():
        crudo = boto3.client("s3", region_name="us-east-1", config=Config(signature_version="s3v4"))
        crudo.create_bucket(Bucket=BUCKET)
        crudo.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        yield AlmacenS3(S3StorageClient(crudo, BUCKET), crudo)


@pytest.fixture
def whatsapp() -> WhatsAppSimulado:
    return WhatsAppSimulado()


@pytest.fixture
def ctx(s3: AlmacenS3, whatsapp: WhatsAppSimulado) -> Contexto:
    return Contexto(ahora=AHORA, s3=s3.cliente, whatsapp=whatsapp)


def inactivo(motor: Engine, id_caso: str, desde: timedelta) -> None:
    """Deja la última actividad del caso `desde` antes del reloj de las pruebas."""
    with motor.begin() as conexion:
        conexion.execute(
            text("UPDATE casos SET ultima_actividad = :t WHERE id_caso = :c"), {"t": AHORA - desde, "c": id_caso}
        )
