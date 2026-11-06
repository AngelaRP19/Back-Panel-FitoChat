"""Consultas guardadas de los reportes (D-19): en memoria y en DynamoDB (con moto), mismo contrato."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import boto3
import pytest
from moto import mock_aws

from app.infrastructure import consultas_store as modulo
from app.infrastructure.consultas_store import (
    Consulta,
    ConsultasDesactivadas,
    ConsultasDynamoDB,
    ConsultasEnMemoria,
    get_consultas_store,
    nuevo_id,
)

TABLA = "consultas-prueba"
AHORA = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


def consulta(**cambios: Any) -> Consulta:
    datos: dict[str, Any] = {
        "id": nuevo_id(),
        "nombre": "Trips en San Luis",
        "tipo": "casos",
        "descripcion": "Casos y estados",
        "filtros": {"periodo": "30d", "vereda": "SAN_LUIS", "categoria": "TRIPS,BOTRYTIS"},
        "visibilidad": "equipo",
        "creada_por": "LID-000001",
        "creada_por_nombre": "Laura Líder",
        "creada_en": AHORA,
    }
    return Consulta(**{**datos, **cambios})


@pytest.fixture
def dynamo() -> Iterator[ConsultasDynamoDB]:
    with mock_aws():
        cliente = boto3.client("dynamodb", region_name="us-east-1")
        cliente.create_table(
            TableName=TABLA,
            KeySchema=[{"AttributeName": "id_consulta", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id_consulta", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield ConsultasDynamoDB(cliente, TABLA)


@pytest.fixture(params=["memoria", "dynamo"])
def almacen(request: pytest.FixtureRequest) -> Any:
    if request.param == "memoria":
        return ConsultasEnMemoria()
    return request.getfixturevalue("dynamo")


def test_el_id_lleva_prefijo_y_es_unico() -> None:
    ids = {nuevo_id() for _ in range(200)}
    assert len(ids) == 200 and all(i.startswith("CNS-") for i in ids)


def test_guarda_y_lee_la_consulta_completa(almacen: Any) -> None:
    c = consulta()
    almacen.guardar(c)
    assert almacen.obtener(c.id) == c
    assert almacen.listar() == [c]


def test_consulta_sin_filtros_ni_fecha(almacen: Any) -> None:
    c = consulta(filtros={}, creada_en=None, visibilidad="yo")
    almacen.guardar(c)
    assert almacen.obtener(c.id) == c


def test_borrar_y_obtener_lo_que_no_existe(almacen: Any) -> None:
    c = consulta()
    almacen.guardar(c)
    almacen.borrar(c.id)
    almacen.borrar("CNS-NOEXISTE")  # no falla
    assert almacen.obtener(c.id) is None and almacen.listar() == []


def test_dynamo_lista_todas_las_paginas(dynamo: ConsultasDynamoDB) -> None:
    for i in range(30):
        dynamo.guardar(consulta(nombre=f"Consulta {i:02d}", descripcion="x" * 20_000))  # ~600 KB > 1 página de Scan
    assert len(dynamo.listar()) == 30


def test_desactivadas_no_guardan_y_listan_vacio() -> None:
    almacen = ConsultasDesactivadas()
    with pytest.raises(RuntimeError):
        almacen.guardar(consulta())
    assert almacen.listar() == [] and almacen.obtener("CNS-1") is None
    almacen.borrar("CNS-1")


def test_sin_tabla_en_local_usa_memoria() -> None:
    get_consultas_store.cache_clear()
    try:
        assert get_consultas_store() is modulo._EN_MEMORIA
    finally:
        get_consultas_store.cache_clear()
