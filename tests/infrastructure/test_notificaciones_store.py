"""Notificaciones de la campana (D-20): en memoria y en DynamoDB (con moto), mismo contrato."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import pytest
from moto import mock_aws

from app.infrastructure import notificaciones_store as modulo
from app.infrastructure.notificaciones_store import (
    Notificacion,
    NotificacionesDesactivadas,
    NotificacionesDynamoDB,
    NotificacionesEnMemoria,
    desde_visibles,
    get_notificaciones_store,
    nuevo_id,
)

TABLA = "notificaciones-prueba"
AHORA = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


def aviso(destinatario: str = "LID-000001", hace: timedelta = timedelta(0), **cambios: Any) -> Notificacion:
    datos: dict[str, Any] = {
        "destinatario": destinatario,
        "id": nuevo_id(),
        "en": AHORA - hace,
        "tipo": "CASO_SIN_TECNICO",
        "titulo": "Caso nuevo sin técnico",
        "texto": "#0241 · Ana Pérez",
        "enlace": "/casos/CAS-00000241",
    }
    return Notificacion(**{**datos, **cambios})


@pytest.fixture
def dynamo() -> Iterator[NotificacionesDynamoDB]:
    with mock_aws():
        cliente = boto3.client("dynamodb", region_name="us-east-1")
        cliente.create_table(
            TableName=TABLA,
            KeySchema=[
                {"AttributeName": "destinatario", "KeyType": "HASH"},
                {"AttributeName": "fecha_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "destinatario", "AttributeType": "S"},
                {"AttributeName": "fecha_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield NotificacionesDynamoDB(cliente, TABLA)


@pytest.fixture(params=["memoria", "dynamo"])
def almacen(request: pytest.FixtureRequest) -> Any:
    if request.param == "memoria":
        return NotificacionesEnMemoria()
    return request.getfixturevalue("dynamo")


def test_el_id_lleva_prefijo_y_es_unico() -> None:
    ids = {nuevo_id() for _ in range(200)}
    assert len(ids) == 200 and all(i.startswith("NOT-") for i in ids)


def test_publica_y_lista_de_la_mas_reciente_a_la_mas_antigua(almacen: Any) -> None:
    vieja = aviso(hace=timedelta(hours=5), titulo="Vieja")
    nueva = aviso(hace=timedelta(hours=1), titulo="Nueva")
    almacen.publicar(vieja)
    almacen.publicar(nueva)
    lista = almacen.listar("LID-000001", desde_visibles(AHORA))
    assert [n.titulo for n in lista] == ["Nueva", "Vieja"]
    assert lista[0] == nueva and lista[0].leida is False


def test_cada_cuenta_ve_solo_lo_suyo(almacen: Any) -> None:
    almacen.publicar(aviso("LID-000001"))
    almacen.publicar(aviso("TEC-000001", tipo="CASO_ASIGNADO"))
    assert [n.tipo for n in almacen.listar("TEC-000001", desde_visibles(AHORA))] == ["CASO_ASIGNADO"]


def test_solo_los_ultimos_7_dias(almacen: Any) -> None:
    almacen.publicar(aviso(hace=timedelta(days=8), titulo="Fuera"))
    almacen.publicar(aviso(hace=timedelta(days=6), titulo="Dentro"))
    assert [n.titulo for n in almacen.listar("LID-000001", desde_visibles(AHORA))] == ["Dentro"]


def test_marcar_leidas_cambia_solo_las_no_leidas(almacen: Any) -> None:
    almacen.publicar(aviso(hace=timedelta(hours=1)))
    almacen.publicar(aviso(hace=timedelta(hours=2)))
    almacen.publicar(aviso("TEC-000001"))
    assert almacen.marcar_leidas("LID-000001", desde_visibles(AHORA)) == 2
    assert all(n.leida for n in almacen.listar("LID-000001", desde_visibles(AHORA)))
    assert almacen.marcar_leidas("LID-000001", desde_visibles(AHORA)) == 0
    assert not almacen.listar("TEC-000001", desde_visibles(AHORA))[0].leida  # la otra cuenta no cambia


def test_dynamo_guarda_el_ttl_de_30_dias(dynamo: NotificacionesDynamoDB) -> None:
    n = aviso()
    dynamo.publicar(n)
    item = dynamo._cliente.scan(TableName=TABLA)["Items"][0]
    assert int(item["expira_en"]["N"]) == int((n.en + timedelta(days=30)).timestamp())
    assert item["fecha_id"]["S"] == f"{n.en.isoformat()}#{n.id}"


def test_desactivadas_no_rompen_nada() -> None:
    almacen = NotificacionesDesactivadas()
    almacen.publicar(aviso())
    assert almacen.listar("LID-000001", desde_visibles(AHORA)) == []
    assert almacen.marcar_leidas("LID-000001", desde_visibles(AHORA)) == 0


def test_sin_tabla_en_local_usa_memoria() -> None:
    get_notificaciones_store.cache_clear()
    try:
        assert get_notificaciones_store() is modulo._EN_MEMORIA
    finally:
        get_notificaciones_store.cache_clear()
