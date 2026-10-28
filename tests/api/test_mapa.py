"""B-09 · Mapa de vigilancia (GET /mapa/casos): casos confirmados con ubicación y nivel de concentración."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.modules.panel_api.mapa.router import nivel
from tests.api.conftest import Mundo
from tests.api.conftest_alertas import construir_zona
from tests.api.territorio import crear_caso, registrar_diagnostico

URL = "/api/v1/mapa/casos"


def pedir(api: TestClient, mundo: Mundo, **params: Any) -> Any:
    r = api.get(URL, params=params, headers=mundo.lider.headers)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize(
    ("vecinos", "umbral", "esperado"),
    [(1, 3, 1), (2, 3, 2), (3, 3, 4), (5, 3, 4), (2, 5, 2), (3, 5, 3), (4, 5, 3), (5, 5, 4)],
)
def test_cortes_de_concentracion(vecinos: int, umbral: int, esperado: int) -> None:
    assert nivel(vecinos, umbral) == esperado


def test_casos_confirmados_con_su_concentracion(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    zona = construir_zona(api, mundo, migrated)  # 3 casos de Trips a menos de 3,5 km: el umbral es 3
    r = pedir(api, mundo)
    assert {c["id"] for c in r["casos"]} == set(zona.casos)
    primero = r["casos"][0]
    assert set(primero) == {
        "id", "codigo", "categoria", "resultado_tecnico", "especie", "productor_nombre", "finca_id", "finca_nombre",
        "vereda", "fecha", "latitud", "longitud", "concentracion",
    }  # fmt: skip
    assert all(c["concentracion"] == 4 and c["categoria"] == "TRIPS" for c in r["casos"])
    assert primero["codigo"].startswith("#") and primero["especie"] == "GULUPA"
    assert r["conteos"]["TRIPS"] == 3 and r["conteos"]["BOTRYTIS"] == 0 and len(r["conteos"]) == 5


def test_una_categoria_que_no_participa_no_tiene_concentracion(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "Registrado", tecnico=mundo.t1.id)
    registrar_diagnostico(migrated, caso, "OTRA_PATOLOGIA", "BOTRYTIS")
    [punto] = pedir(api, mundo)["casos"]
    assert punto["categoria"] == "BOTRYTIS" and punto["concentracion"] is None


def test_un_caso_solo_es_nivel_1(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "Registrado", tecnico=mundo.t1.id)
    registrar_diagnostico(migrated, caso, "CONFIRMADO", "TRIPS")
    assert [c["concentracion"] for c in pedir(api, mundo)["casos"]] == [1]


def test_sin_diagnostico_o_no_identificado_no_aparece(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    crear_caso(migrated, mundo.cultivo1, "GULUPA", "En revisión", tecnico=mundo.t1.id)
    caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "Registrado", tecnico=mundo.t1.id)
    registrar_diagnostico(migrated, caso, "NO_IDENTIFICADO", None)
    assert pedir(api, mundo)["casos"] == []


def test_filtra_por_especie(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    construir_zona(api, mundo, migrated)
    assert pedir(api, mundo, especie="MARACUYA")["casos"] == []
    assert len(pedir(api, mundo, especie="GULUPA")["casos"]) == 3


def test_fechas_invertidas_422(api: TestClient, mundo: Mundo) -> None:
    r = api.get(URL, params={"desde": "2026-10-10", "hasta": "2026-10-01"}, headers=mundo.lider.headers)
    assert r.status_code == 422


def test_solo_el_lider(api: TestClient, mundo: Mundo) -> None:
    assert api.get(URL, headers=mundo.t1.headers).status_code == 403
    assert api.get(URL).status_code == 401
