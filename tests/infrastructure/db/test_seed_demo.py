"""Siembra y limpieza de los datos demo: pruebas que modifican datos (cada una con su propia transacción)."""

from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from botocore.config import Config
from moto import mock_aws
from sqlalchemy.orm import Session

from app.infrastructure.db import procedimientos as sp
from app.infrastructure.db import seed_demo as demo
from app.infrastructure.s3_storage_client import ErrorBorradoS3, S3StorageClient
from tests.infrastructure.db.demo_helpers import AHORA, BUCKET, contar, escalar, filas, versiones


@pytest.fixture
def bucket() -> Iterator[tuple[S3StorageClient, Any]]:
    with mock_aws():
        crudo = boto3.client("s3", region_name="us-east-1", config=Config(signature_version="s3v4"))
        crudo.create_bucket(Bucket=BUCKET)
        crudo.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        yield S3StorageClient(crudo, BUCKET), crudo


# --- idempotencia ------------------------------------------------------------------------------------------------


def test_sembrar_dos_veces_no_duplica(session: Session, bucket: tuple[S3StorageClient, Any]) -> None:
    s3, crudo = bucket
    primera = demo.sembrar(session, s3, AHORA)
    antes, objetos = contar(session), len(versiones(crudo))
    segunda = demo.sembrar(session, s3, AHORA)
    assert primera["ya_estaba"] is False and segunda == {"ya_estaba": True}
    assert contar(session) == antes and len(versiones(crudo)) == objetos


def test_ya_sembrado_detecta_los_datos(session: Session, bucket: tuple[S3StorageClient, Any]) -> None:
    assert demo.ya_sembrado(session) is False
    demo.sembrar(session, bucket[0], AHORA)
    assert demo.ya_sembrado(session) is True


def test_sembrar_si_falla_la_subida_no_deja_fotos_huerfanas(
    session: Session, bucket: tuple[S3StorageClient, Any]
) -> None:
    s3, crudo = bucket

    class Fallido(S3StorageClient):
        subidas = 0

        def subir(self, clave: str, contenido: bytes, content_type: str = "image/webp") -> None:
            Fallido.subidas += 1
            if Fallido.subidas == 6:
                raise OSError("S3 no responde")
            super().subir(clave, contenido, content_type)

    with pytest.raises(OSError, match="S3 no responde"):
        with session.begin_nested():
            demo.sembrar(session, Fallido(crudo, BUCKET), AHORA)
    assert versiones(crudo) == []  # lo que alcanzó a subir se retiró
    assert demo.ya_sembrado(session) is False  # y la base se revirtió


# --- limpieza ----------------------------------------------------------------------------------------------------


def test_limpiar_deja_la_base_y_el_bucket_sin_rastros(session: Session, bucket: tuple[S3StorageClient, Any]) -> None:
    s3, crudo = bucket
    antes = contar(session)
    demo.sembrar(session, s3, AHORA)
    assert contar(session) != antes and versiones(crudo)
    borrados = demo.limpiar(session, s3)
    assert contar(session) == antes
    assert versiones(crudo) == []  # ni versiones ni marcadores de borrado
    assert borrados["casos"] == 22 and borrados["objetos_s3"] == borrados["fotografias"]
    assert borrados["versiones_s3"] == borrados["objetos_s3"] > 0


def test_limpiar_elimina_tambien_las_versiones_anteriores_de_una_foto(
    session: Session, bucket: tuple[S3StorageClient, Any]
) -> None:
    s3, crudo = bucket
    demo.sembrar(session, s3, AHORA)
    clave = escalar(session, "SELECT ruta_fotografia FROM fotografias ORDER BY id_fotografia LIMIT 1")
    s3.subir(clave, b"otra version")  # segunda versión del mismo objeto
    assert len(s3.listar_versiones(clave)) == 2
    demo.limpiar(session, s3)
    assert versiones(crudo) == []


def test_limpiar_no_toca_lo_que_no_es_demo(session: Session, bucket: tuple[S3StorageClient, Any]) -> None:
    from tests.infrastructure.db.factories import escenario

    s3, crudo = bucket
    real = escenario(session)  # un productor "real" (sin marca) con su finca y cultivo
    s3.subir("evidencias/2026/10/FOT-90000001.webp", b"foto real")
    demo.sembrar(session, s3, AHORA)
    demo.limpiar(session, s3)
    assert session.get(type(real.productor), real.productor.id_productor) is not None
    assert (
        escalar(session, "SELECT count(*) FROM fincas") == 1 and escalar(session, "SELECT count(*) FROM cultivos") == 1
    )
    assert [v["Key"] for v in versiones(crudo)] == ["evidencias/2026/10/FOT-90000001.webp"]
    assert (
        escalar(session, "SELECT count(*) FROM estados") == 8
    )  # los 7 del proyecto + el del escenario: el catálogo se conserva


def test_limpiar_dos_veces_no_falla(session: Session, bucket: tuple[S3StorageClient, Any]) -> None:
    s3, _ = bucket
    demo.sembrar(session, s3, AHORA)
    demo.limpiar(session, s3)
    segunda = demo.limpiar(session, s3)
    assert segunda["casos"] == 0 and segunda["objetos_s3"] == 0 and segunda["versiones_s3"] == 0


def test_se_puede_volver_a_sembrar_despues_de_limpiar(session: Session, bucket: tuple[S3StorageClient, Any]) -> None:
    s3, crudo = bucket
    demo.sembrar(session, s3, AHORA)
    demo.limpiar(session, s3)
    otra = demo.sembrar(session, s3, AHORA)
    assert otra["ya_estaba"] is False and len(versiones(crudo)) == otra["fotos"]


def test_si_s3_falla_no_se_borra_ninguna_fila(session: Session, bucket: tuple[S3StorageClient, Any]) -> None:
    s3, crudo = bucket
    demo.sembrar(session, s3, AHORA)
    antes = contar(session)

    class SinPermiso(S3StorageClient):
        def eliminar_todas_las_versiones(self, clave: str) -> int:
            raise ErrorBorradoS3("AccessDenied")

    with pytest.raises(ErrorBorradoS3):
        demo.limpiar(session, SinPermiso(crudo, BUCKET))
    assert contar(session) == antes and demo.ya_sembrado(session)


def test_una_cuenta_con_auditoria_no_se_puede_borrar(session: Session, bucket: tuple[S3StorageClient, Any]) -> None:
    """La auditoría es inmutable: si alguien usó una cuenta demo en el panel, esa cuenta se conserva."""
    s3, _ = bucket
    demo.sembrar(session, s3, AHORA)
    tecnico = escalar(session, "SELECT id_tecnico FROM tecnicos WHERE nombre_tecnico LIKE 'Ana Torres%'")
    session.execute(
        sp.REGISTRAR_AUDITORIA,
        {
            "origen": "TECNICO",
            "id_actor": tecnico,
            "accion": "PRUEBA",
            "entidad": "casos",
            "descripcion": "Uso real de la cuenta",
            "valor_anterior": None,
            "valor_nuevo": None,
            "resultado": "EXITO",
            "fecha": None,
        },
    )
    borrados = demo.limpiar(session, s3)
    assert borrados["tecnicos"] == 2 and borrados["lideres_tecnicos"] == 1
    assert [f["id_tecnico"] for f in filas(session, "SELECT id_tecnico FROM tecnicos")] == [tecnico]
