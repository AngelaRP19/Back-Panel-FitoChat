"""Estado de las conversaciones: en memoria y en DynamoDB (con moto)."""

import time
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws

from app.core import config
from app.infrastructure import conversaciones_store as modulo
from app.infrastructure.conversaciones_store import (
    Conversacion,
    ConversacionesDynamoDB,
    ConversacionesEnMemoria,
    get_conversaciones_store,
)

TABLA = "conversaciones-prueba"


@pytest.fixture
def dynamo() -> Iterator[ConversacionesDynamoDB]:
    with mock_aws():
        cliente = boto3.client("dynamodb", region_name="us-east-1")
        cliente.create_table(
            TableName=TABLA,
            KeySchema=[{"AttributeName": "telefono", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "telefono", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield ConversacionesDynamoDB(cliente, TABLA, vigencia_dias=7)


@pytest.fixture(params=["memoria", "dynamo"])
def almacen(request: pytest.FixtureRequest) -> Any:
    """Las dos implementaciones cumplen el mismo contrato."""
    if request.param == "memoria":
        return ConversacionesEnMemoria()
    return request.getfixturevalue("dynamo")


def test_guardar_y_leer(almacen: Any) -> None:
    conv = Conversacion("573001112233", "CUESTIONARIO", "CAS-00000001", {"pregunta_actual": "¿Desde cuándo?", "n": 2})
    almacen.guardar(conv)
    leida = almacen.obtener("573001112233")
    assert leida == conv and leida.contexto["pregunta_actual"] == "¿Desde cuándo?"


def test_lo_que_no_existe_es_none(almacen: Any) -> None:
    assert almacen.obtener("573009998877") is None


def test_sin_caso_en_curso(almacen: Any) -> None:
    almacen.guardar(Conversacion("573001112233", "INICIO"))
    leida = almacen.obtener("573001112233")
    assert leida is not None and leida.caso_id is None and leida.contexto == {}


def test_guardar_otra_vez_reemplaza(almacen: Any) -> None:
    almacen.guardar(Conversacion("573001112233", "INICIO"))
    almacen.guardar(Conversacion("573001112233", "FOTOS", "CAS-00000002"))
    leida = almacen.obtener("573001112233")
    assert (leida.paso, leida.caso_id) == ("FOTOS", "CAS-00000002")


def test_borrar_y_borrar_lo_inexistente(almacen: Any) -> None:
    almacen.guardar(Conversacion("573001112233", "INICIO"))
    almacen.borrar("573001112233")
    almacen.borrar("573001112233")  # no falla
    assert almacen.obtener("573001112233") is None


def test_cada_telefono_tiene_su_documento(almacen: Any) -> None:
    almacen.guardar(Conversacion("573001112233", "FOTOS"))
    almacen.guardar(Conversacion("573009998877", "ZONA"))
    assert almacen.obtener("573001112233").paso == "FOTOS"
    assert almacen.obtener("573009998877").paso == "ZONA"


def test_el_texto_con_tildes_y_enes_se_conserva(almacen: Any) -> None:
    almacen.guardar(Conversacion("573001112233", "ZONA", contexto={"zona": "la quebrada del señor Ñáñez"}))
    assert almacen.obtener("573001112233").contexto["zona"] == "la quebrada del señor Ñáñez"


def test_dynamodb_guarda_el_atributo_que_expira(dynamo: ConversacionesDynamoDB) -> None:
    """`expira_en` es el TTL de la tabla: las conversaciones abandonadas se limpian solas."""
    ahora = time.time()
    dynamo.guardar(Conversacion("573001112233", "INICIO", ultima_actividad=ahora))
    item = dynamo._cliente.get_item(TableName=TABLA, Key={"telefono": {"S": "573001112233"}})["Item"]
    assert int(item["expira_en"]["N"]) == int(ahora) + 7 * 86_400


def test_la_conversacion_nueva_marca_su_ultima_actividad() -> None:
    antes = time.time()
    assert antes - 1 <= Conversacion("57", "INICIO").ultima_actividad <= time.time() + 1


# --- cuál se usa -----------------------------------------------------------------------------------------------------------


def usar(monkeypatch: pytest.MonkeyPatch, **cambios: Any) -> None:
    get_conversaciones_store.cache_clear()
    ajustes = config.get_settings().model_copy(update=cambios)
    monkeypatch.setattr(modulo, "get_settings", lambda: ajustes)


def test_sin_tabla_se_usa_la_memoria_fuera_de_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    usar(monkeypatch, dynamodb_tabla_conversaciones=None, app_env="local")
    assert isinstance(get_conversaciones_store(), ConversacionesEnMemoria)
    get_conversaciones_store.cache_clear()


def test_en_prod_la_tabla_es_obligatoria(monkeypatch: pytest.MonkeyPatch) -> None:
    usar(monkeypatch, dynamodb_tabla_conversaciones=None)
    monkeypatch.setattr(type(config.get_settings()), "en_prod", property(lambda self: True))
    with pytest.raises(RuntimeError, match="DYNAMODB_TABLA_CONVERSACIONES"):
        get_conversaciones_store()
    get_conversaciones_store.cache_clear()


def test_con_tabla_se_usa_dynamodb(monkeypatch: pytest.MonkeyPatch) -> None:
    with mock_aws():
        usar(monkeypatch, dynamodb_tabla_conversaciones=TABLA, aws_endpoint_url=None)
        assert isinstance(get_conversaciones_store(), ConversacionesDynamoDB)
        get_conversaciones_store.cache_clear()
