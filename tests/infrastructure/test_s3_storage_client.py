"""Cliente de S3 con moto: URLs prefirmadas y borrado físico con el versionado activo (guía §6.7)."""

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.config import Config
from moto import mock_aws

from app.infrastructure.s3_storage_client import ErrorBorradoS3, S3StorageClient

BUCKET = "anuc-evidencias-prueba"
CLAVE = "evidencias/2026/10/FOT-00000001.webp"


@pytest.fixture
def s3() -> Iterator[tuple[S3StorageClient, Any]]:
    """Bucket con versionado, como el de AWS."""
    with mock_aws():
        crudo = boto3.client("s3", region_name="us-east-1", config=Config(signature_version="s3v4"))
        crudo.create_bucket(Bucket=BUCKET)
        crudo.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        yield S3StorageClient(crudo, BUCKET), crudo


def _versiones(crudo: Any) -> list[dict[str, Any]]:
    respuesta = crudo.list_object_versions(Bucket=BUCKET)
    return [*respuesta.get("Versions", []), *respuesta.get("DeleteMarkers", [])]


def test_sube_y_encuentra_un_objeto(s3: tuple[S3StorageClient, Any]) -> None:
    cliente, crudo = s3
    assert not cliente.existe(CLAVE)
    cliente.subir(CLAVE, b"contenido")
    assert cliente.existe(CLAVE)
    objeto = crudo.get_object(Bucket=BUCKET, Key=CLAVE)
    assert objeto["Body"].read() == b"contenido" and objeto["ContentType"] == "image/webp"


def test_existe_no_traga_otros_errores() -> None:
    from botocore.exceptions import ClientError

    falso = MagicMock()
    falso.head_object.side_effect = ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject")
    with pytest.raises(ClientError):
        S3StorageClient(falso, BUCKET).existe(CLAVE)


def test_url_prefirmada_apunta_al_objeto_y_vence(s3: tuple[S3StorageClient, Any]) -> None:
    cliente, _ = s3
    url = cliente.url_prefirmada(CLAVE, expira_segundos=120)
    assert BUCKET in url and CLAVE in url
    assert "X-Amz-Expires=120" in url or "Expires=" in url


def test_la_url_prefirmada_dura_10_minutos_por_defecto(s3: tuple[S3StorageClient, Any]) -> None:
    cliente, _ = s3
    assert "X-Amz-Expires=600" in cliente.url_prefirmada(CLAVE)


# --- borrado físico con versionado -----------------------------------------------------------------------------------


def test_un_delete_simple_deja_la_foto_recuperable(s3: tuple[S3StorageClient, Any]) -> None:
    """Es el problema que resuelve el cliente: con versionado, borrar solo agrega un marcador."""
    cliente, crudo = s3
    cliente.subir(CLAVE, b"foto")
    crudo.delete_object(Bucket=BUCKET, Key=CLAVE)
    assert not cliente.existe(CLAVE)  # parece borrada...
    assert len(_versiones(crudo)) == 2  # ...pero la versión sigue ahí, más el marcador


def test_elimina_todas_las_versiones_y_los_marcadores(s3: tuple[S3StorageClient, Any]) -> None:
    cliente, crudo = s3
    cliente.subir(CLAVE, b"version 1")
    cliente.subir(CLAVE, b"version 2")
    cliente.subir(CLAVE, b"version 3")
    crudo.delete_object(Bucket=BUCKET, Key=CLAVE)  # marcador de borrado
    assert len(cliente.listar_versiones(CLAVE)) == 4
    assert cliente.eliminar_todas_las_versiones(CLAVE) == 4
    assert _versiones(crudo) == []  # no queda nada recuperable


def test_borrar_un_objeto_que_no_existe_devuelve_cero(s3: tuple[S3StorageClient, Any]) -> None:
    cliente, _ = s3
    assert cliente.eliminar_todas_las_versiones("evidencias/2026/10/no-existe.webp") == 0


def test_no_toca_otras_claves_que_comparten_prefijo(s3: tuple[S3StorageClient, Any]) -> None:
    cliente, crudo = s3
    parecida = CLAVE + ".bak"
    otra = "evidencias/2026/10/FOT-00000010.webp"
    for clave in (CLAVE, parecida, otra):
        cliente.subir(clave, b"x")
    assert cliente.eliminar_todas_las_versiones(CLAVE) == 1
    assert {v["Key"] for v in _versiones(crudo)} == {parecida, otra}


def test_listar_versiones_solo_de_la_clave_exacta(s3: tuple[S3StorageClient, Any]) -> None:
    cliente, _ = s3
    cliente.subir(CLAVE, b"x")
    cliente.subir(CLAVE + ".bak", b"x")
    assert [v["Key"] for v in cliente.listar_versiones(CLAVE)] == [CLAVE]


def test_si_s3_rechaza_el_borrado_se_avisa() -> None:
    """Si falla el borrado en S3 no se debe borrar la fila: el error tiene que propagarse."""
    falso = MagicMock()
    falso.get_paginator.return_value.paginate.return_value = [{"Versions": [{"Key": CLAVE, "VersionId": "v1"}]}]
    falso.delete_objects.return_value = {"Errors": [{"Key": CLAVE, "Code": "AccessDenied"}]}
    with pytest.raises(ErrorBorradoS3, match="1 versión"):
        S3StorageClient(falso, BUCKET).eliminar_todas_las_versiones(CLAVE)


def test_borra_en_lotes_de_mil() -> None:
    falso = MagicMock()
    versiones = [{"Key": CLAVE, "VersionId": f"v{i}"} for i in range(2500)]
    falso.get_paginator.return_value.paginate.return_value = [{"Versions": versiones}]
    falso.delete_objects.return_value = {}
    assert S3StorageClient(falso, BUCKET).eliminar_todas_las_versiones(CLAVE) == 2500
    tamanos = [len(llamada.kwargs["Delete"]["Objects"]) for llamada in falso.delete_objects.call_args_list]
    assert tamanos == [1000, 1000, 500]


def test_el_cliente_real_usa_el_endpoint_de_localstack(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import Settings
    from app.infrastructure import s3_storage_client as modulo

    ajustes = Settings(_env_file=None, aws_endpoint_url="http://localstack:4566", s3_bucket_evidencias=BUCKET)  # type: ignore[call-arg]
    monkeypatch.setattr(modulo, "get_settings", lambda: ajustes)
    crudo = modulo.build_boto3_s3_client()
    assert crudo.meta.endpoint_url == "http://localstack:4566"
    assert crudo.meta.config.s3["addressing_style"] == "path"


def test_get_s3_storage_client_exige_el_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import Settings
    from app.infrastructure import s3_storage_client as modulo

    modulo.get_s3_storage_client.cache_clear()
    monkeypatch.setattr(modulo, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]
    with pytest.raises(RuntimeError, match="S3_BUCKET_EVIDENCIAS"):
        modulo.get_s3_storage_client()
    modulo.get_s3_storage_client.cache_clear()
