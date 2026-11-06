"""B-05: opciones de filtro de la bandeja (GET /casos/filtros) y resúmenes de fincas y productores."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from tests.api.conftest import Cuenta, Mundo
from tests.api.territorio import crear_caso


def pedir(api: TestClient, actor: Cuenta, ruta: str) -> Any:
    r = api.get(ruta, headers=actor.headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_filtros_solo_con_lo_que_existe_en_los_casos(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    crear_caso(migrated, mundo.cultivo1, "GULUPA", "Pendiente de revisión")
    crear_caso(migrated, mundo.cultivo1, "GULUPA", "Pendiente de revisión")  # repetidos: una sola opción
    r = pedir(api, mundo.lider, "/api/v1/casos/filtros")
    assert r["veredas"] == [{"id": "CENTRO", "nombre": "Centro"}]
    assert r["fincas"] == [{"id": mundo.finca1["id"], "nombre": "La Esperanza", "vereda": "CENTRO"}]
    assert r["cultivos"] == [
        {"id": mundo.cultivo1, "nombre": r["cultivos"][0]["nombre"], "finca_id": mundo.finca1["id"]}
    ]


def test_el_tecnico_solo_ve_opciones_de_sus_casos(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    crear_caso(migrated, mundo.cultivo1, "GULUPA", "En revisión", tecnico=mundo.t1.id)
    crear_caso(migrated, mundo.cultivo2, "MARACUYA", "En revisión", tecnico=mundo.t2.id)
    assert [v["id"] for v in pedir(api, mundo.t1, "/api/v1/casos/filtros")["veredas"]] == ["CENTRO"]
    assert [v["id"] for v in pedir(api, mundo.t2, "/api/v1/casos/filtros")["veredas"]] == ["SAN_LUIS"]
    assert [v["id"] for v in pedir(api, mundo.lider, "/api/v1/casos/filtros")["veredas"]] == ["CENTRO", "SAN_LUIS"]


def test_sin_casos_no_hay_opciones(api: TestClient, mundo: Mundo) -> None:
    assert pedir(api, mundo.lider, "/api/v1/casos/filtros") == {"veredas": [], "fincas": [], "cultivos": []}


def test_resumen_de_fincas(api: TestClient, mundo: Mundo) -> None:
    r = pedir(api, mundo.lider, "/api/v1/fincas/resumen")
    assert r == {"fincas": 2, "cultivos": 2, "veredas": ["Centro", "San Luis"]}


def test_resumen_de_productores(api: TestClient, mundo: Mundo) -> None:
    api.patch(
        f"/api/v1/productores/{mundo.productor2['id']}/estado", json={"activo": False}, headers=mundo.lider.headers
    )
    assert pedir(api, mundo.lider, "/api/v1/productores/resumen") == {"total": 2, "activos": 1}


@pytest.mark.parametrize("ruta", ["/api/v1/fincas/resumen", "/api/v1/productores/resumen"])
def test_los_resumenes_son_del_lider(api: TestClient, mundo: Mundo, ruta: str) -> None:
    assert api.get(ruta, headers=mundo.t1.headers).status_code == 403
    assert api.get(ruta).status_code == 401
