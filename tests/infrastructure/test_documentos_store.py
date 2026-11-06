"""Documentación fitosanitaria (D-18): metadatos en memoria y en DynamoDB (con moto), mismo contrato; PDF en memoria."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import boto3
import pytest
from moto import mock_aws

from app.infrastructure import documentos_store as modulo
from app.infrastructure.documentos_store import (
    ArchivosEnMemoria,
    Documento,
    DocumentosDesactivados,
    DocumentosDynamoDB,
    DocumentosEnMemoria,
    clave_s3,
    get_archivos_documentos,
    get_documentos_store,
    nuevo_id,
)

TABLA = "documentos-prueba"


def documento(**cambios: Any) -> Documento:
    id_documento = nuevo_id()
    datos: dict[str, Any] = {
        "id": id_documento,
        "titulo": "Ficha de Trips",
        "tipo": "FICHA_TECNICA",
        "categoria": "TRIPS",
        "clave_s3": clave_s3(id_documento),
        "tamano_bytes": 1234,
        "subido_por": "LID-000001",
        "subido_por_nombre": "Laura Líder",
        "subido_en": datetime(2026, 10, 27, 15, 0, tzinfo=UTC),
    }
    return Documento(**{**datos, **cambios})


@pytest.fixture
def dynamo() -> Iterator[DocumentosDynamoDB]:
    with mock_aws():
        cliente = boto3.client("dynamodb", region_name="us-east-1")
        cliente.create_table(
            TableName=TABLA,
            KeySchema=[{"AttributeName": "id_documento", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id_documento", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield DocumentosDynamoDB(cliente, TABLA)


@pytest.fixture(params=["memoria", "dynamo"])
def almacen(request: pytest.FixtureRequest) -> Any:
    if request.param == "memoria":
        return DocumentosEnMemoria()
    return request.getfixturevalue("dynamo")


def test_el_id_lleva_prefijo_y_la_clave_va_en_documentos() -> None:
    ids = {nuevo_id() for _ in range(200)}
    assert len(ids) == 200 and all(i.startswith("DOC-") for i in ids)
    assert clave_s3("DOC-1") == "documentos/DOC-1.pdf"


@pytest.mark.parametrize("categoria", ["TRIPS", None])
def test_guarda_y_lee_el_documento(almacen: Any, categoria: str | None) -> None:
    d = documento(categoria=categoria)
    almacen.guardar(d)
    assert almacen.obtener(d.id) == d and almacen.listar() == [d]
    assert almacen.obtener("DOC-NOEXISTE") is None


def test_desactivados_no_guardan_y_listan_vacio() -> None:
    almacen = DocumentosDesactivados()
    with pytest.raises(RuntimeError):
        almacen.guardar(documento())
    assert almacen.listar() == [] and almacen.obtener("DOC-1") is None


def test_archivos_en_memoria() -> None:
    archivos = ArchivosEnMemoria()
    archivos.subir("documentos/a.pdf", b"%PDF-1")
    assert archivos.descargar("documentos/a.pdf") == b"%PDF-1"
    with pytest.raises(FileNotFoundError):
        archivos.descargar("documentos/b.pdf")


def test_sin_tabla_ni_bucket_en_local_usa_memoria() -> None:
    get_documentos_store.cache_clear()
    try:
        assert get_documentos_store() is modulo._EN_MEMORIA
        assert get_archivos_documentos() is modulo._ARCHIVOS_EN_MEMORIA
    finally:
        get_documentos_store.cache_clear()
