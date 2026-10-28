"""B-06 · Acciones masivas de productores (pantalla 21): exportar CSV, activar/desactivar y mensaje por WhatsApp."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.core.guardrail import MENSAJE_BLOQUEADO
from app.infrastructure.whatsapp_client import WhatsAppSimulado
from app.main import app
from app.modules.panel_api.alertas.router import get_whatsapp
from tests.api.conftest import Mundo, auditoria_de
from tests.api.territorio import crear_productor

URL = "/api/v1/productores"


@pytest.fixture
def whatsapp(api: TestClient) -> Iterator[WhatsAppSimulado]:
    cliente = WhatsAppSimulado()
    app.dependency_overrides[get_whatsapp] = lambda: cliente
    yield cliente


# --- exportar ------------------------------------------------------------------------------------------------------


def test_exporta_la_seleccion_en_csv(api: TestClient, mundo: Mundo) -> None:
    r = api.get(f"{URL}/exportar", params={"ids": mundo.productor1["id"]}, headers=mundo.lider.headers)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    texto = r.content.decode("utf-8")
    assert texto.startswith("﻿")  # BOM: Excel lee las tildes
    filas = texto.lstrip("﻿").strip().split("\r\n")
    assert filas[0] == "Nombre;WhatsApp;Vereda;Fincas;Estado;Casos activos"
    assert len(filas) == 2 and filas[1].startswith(f"Ana Pérez;{mundo.productor1['celular']};")


def test_sin_ids_exporta_la_lista_filtrada(api: TestClient, mundo: Mundo) -> None:
    api.patch(f"{URL}/{mundo.productor2['id']}/estado", json={"activo": False}, headers=mundo.lider.headers)
    r = api.get(f"{URL}/exportar", params={"estado": "activo"}, headers=mundo.lider.headers)
    filas = r.content.decode("utf-8").lstrip("﻿").strip().split("\r\n")
    assert [f.split(";")[0] for f in filas[1:]] == ["Ana Pérez"]


def test_neutraliza_formulas_en_el_csv(api: TestClient, mundo: Mundo) -> None:
    malicioso = crear_productor(api, mundo.lider, 9, nombre="=HYPERLINK(1)")
    r = api.get(f"{URL}/exportar", params={"ids": malicioso["id"]}, headers=mundo.lider.headers)
    assert "\r\n'=HYPERLINK(1);" in r.content.decode("utf-8")


# --- estado masivo -------------------------------------------------------------------------------------------------


def test_desactiva_varios_y_audita_cada_uno(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    ids = [mundo.productor1["id"], mundo.productor2["id"], "PRO-999999"]
    r = api.post(f"{URL}/estado", json={"ids": ids, "activo": False}, headers=mundo.lider.headers)
    assert r.status_code == 200 and r.json() == {"actualizados": 2, "solicitados": 3}
    assert api.get(f"{URL}/resumen", headers=mundo.lider.headers).json()["activos"] == 0
    assert len([a for a in auditoria_de(migrated) if a["accion"] == "ESTADO_PRODUCTOR"]) == 2
    again = api.post(f"{URL}/estado", json={"ids": ids, "activo": False}, headers=mundo.lider.headers)
    assert again.json() == {"actualizados": 0, "solicitados": 3}  # ya estaban así: no cuenta ni audita


def test_estado_sin_seleccion_422(api: TestClient, mundo: Mundo) -> None:
    r = api.post(f"{URL}/estado", json={"ids": [], "activo": True}, headers=mundo.lider.headers)
    assert r.status_code == 422 and "ids" in r.json()["campos"]


# --- mensaje -------------------------------------------------------------------------------------------------------


def test_envia_solo_a_los_activos(api: TestClient, mundo: Mundo, migrated: Engine, whatsapp: WhatsAppSimulado) -> None:
    api.patch(f"{URL}/{mundo.productor2['id']}/estado", json={"activo": False}, headers=mundo.lider.headers)
    ids = [mundo.productor1["id"], mundo.productor2["id"]]
    mensaje = "Reunión de productores el sábado a las 9 en la sede de ANUC."
    r = api.post(f"{URL}/mensaje", json={"ids": ids, "mensaje": f"  {mensaje}  "}, headers=mundo.lider.headers)
    assert r.status_code == 200 and r.json() == {"enviados": 1, "omitidos": 1}
    assert whatsapp.enviados == [("57" + mundo.productor1["celular"], mensaje)]
    [registro] = [a for a in auditoria_de(migrated) if a["accion"] == "MENSAJE_PRODUCTORES"]
    assert mundo.productor1["celular"] not in str(registro)  # sin celulares en la auditoría (D-09)


def test_un_envio_fallido_se_cuenta_como_omitido(api: TestClient, mundo: Mundo, whatsapp: WhatsAppSimulado) -> None:
    whatsapp.fallan.add("57" + mundo.productor1["celular"])
    r = api.post(
        f"{URL}/mensaje",
        json={"ids": [mundo.productor1["id"], mundo.productor2["id"]], "mensaje": "Aviso general"},
        headers=mundo.lider.headers,
    )
    assert r.json() == {"enviados": 1, "omitidos": 1}


@pytest.mark.parametrize(
    ("mensaje", "error"),
    [("   ", "Escribe el mensaje para los productores."), ("x" * 501, "Máximo 500 caracteres.")],
)
def test_mensaje_invalido_422(
    api: TestClient, mundo: Mundo, whatsapp: WhatsAppSimulado, mensaje: str, error: str
) -> None:
    r = api.post(
        f"{URL}/mensaje", json={"ids": [mundo.productor1["id"]], "mensaje": mensaje}, headers=mundo.lider.headers
    )
    assert r.status_code == 422 and r.json()["campos"]["mensaje"] == error and whatsapp.enviados == []


def test_el_guardrail_bloquea_tratamientos(api: TestClient, mundo: Mundo, whatsapp: WhatsAppSimulado) -> None:
    r = api.post(
        f"{URL}/mensaje",
        json={"ids": [mundo.productor1["id"]], "mensaje": "Aplique fungicida 2 ml por litro"},
        headers=mundo.lider.headers,
    )
    assert r.status_code == 422 and r.json()["campos"]["mensaje"] == MENSAJE_BLOQUEADO and whatsapp.enviados == []


@pytest.mark.parametrize(
    ("metodo", "ruta", "cuerpo"),
    [
        ("GET", f"{URL}/exportar", None),
        ("POST", f"{URL}/estado", {"ids": ["PRO-000001"], "activo": True}),
        ("POST", f"{URL}/mensaje", {"ids": ["PRO-000001"], "mensaje": "Hola"}),
    ],
)
def test_solo_el_lider(api: TestClient, mundo: Mundo, metodo: str, ruta: str, cuerpo: object) -> None:
    assert api.request(metodo, ruta, json=cuerpo, headers=mundo.t1.headers).status_code == 403
    assert api.request(metodo, ruta, json=cuerpo).status_code == 401
