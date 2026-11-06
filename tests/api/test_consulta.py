"""/consulta/productores-fincas: el técnico solo ve productores, fincas y cultivos de SUS casos (P-07)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.core.roles import Rol
from tests.api.conftest import Cuenta, NuevaCuenta
from tests.api.territorio import crear_caso, crear_cultivo, crear_finca, crear_productor

URL = "/api/v1/consulta/productores-fincas"


@pytest.fixture
def lider(nueva_cuenta: NuevaCuenta) -> Cuenta:
    return nueva_cuenta(Rol.LIDER_TECNICO)


@pytest.fixture
def mundo(
    api: TestClient, lider: Cuenta, catalogos: None, nueva_cuenta: NuevaCuenta, migrated: Engine
) -> dict[str, Any]:
    """
    Dos técnicos y tres productores:
      - Ana (técnica 1): La Esperanza (Centro) con dos cultivos y casos suyos en uno; un tercer cultivo sin casos.
      - Álvaro (técnica 1): El Roble (San Luis), un caso cerrado.
      - Bea (técnico 2): La Colina, un caso de ella; y un caso que fue de la técnica 1 y se reasignó.
    """
    t1, t2 = nueva_cuenta(Rol.TECNICO, 1, nombre="Técnica Uno"), nueva_cuenta(Rol.TECNICO, 2, nombre="Técnico Dos")
    ana = crear_productor(api, lider, 1, nombre="Ana Pérez")
    alvaro = crear_productor(api, lider, 2, nombre="Álvaro Díaz")
    bea = crear_productor(api, lider, 3, nombre="Bea Núñez")
    esperanza = crear_finca(api, lider, ana["id"], "La Esperanza", "Centro", 5.25, -73.17)
    roble = crear_finca(api, lider, alvaro["id"], "El Roble", "San Luis", 5.27, -73.19)
    colina = crear_finca(api, lider, bea["id"], "La Colina", "Centro")
    crear_cultivo(api, lider, esperanza["id"], "Lote 1", "CAMPO_ABIERTO", ("GULUPA", "MARACUYA"))
    crear_cultivo(api, lider, esperanza["id"], "Lote 2", "INVERNADERO", ("GRANADILLA",))
    r = crear_cultivo(api, lider, esperanza["id"], "Lote sin casos", "INVERNADERO", ("GULUPA",))
    c_lote1, c_lote2, c_vacio = (c["id"] for c in r["cultivos"])
    c_roble = crear_cultivo(api, lider, roble["id"], "Lote 1", "INVERNADERO", ("MARACUYA",))["cultivos"][0]["id"]
    c_colina = crear_cultivo(api, lider, colina["id"], "Lote 1", "CAMPO_ABIERTO", ("GULUPA",))["cultivos"][0]["id"]
    casos = {
        "ana_1": crear_caso(migrated, c_lote1, "GULUPA", "En revisión", tecnico=t1.id),
        "ana_2": crear_caso(migrated, c_lote1, "MARACUYA", "Registrado", tecnico=t1.id),
        "ana_3": crear_caso(migrated, c_lote2, "GRANADILLA", "Cerrado", tecnico=t1.id),
        "alvaro": crear_caso(migrated, c_roble, "MARACUYA", "Cerrado", tecnico=t1.id),
        "bea": crear_caso(migrated, c_colina, "GULUPA", "En revisión", tecnico=t2.id),
        "reasignado": crear_caso(migrated, c_colina, "GULUPA", "En revisión", tecnico=t1.id, vigente=False),
    }
    return {
        "t1": t1, "t2": t2, "ana": ana, "alvaro": alvaro, "bea": bea, "esperanza": esperanza, "roble": roble,
        "colina": colina, "casos": casos, "c_vacio": c_vacio,
    }  # fmt: skip


def registros(api: TestClient, tecnico: Cuenta, **filtros: Any) -> list[dict[str, Any]]:
    r = api.get(URL, params=filtros, headers=tecnico.headers)
    assert r.status_code == 200, r.text
    return list(r.json()["items"])


# --- permisos ----------------------------------------------------------------------------------------------------


def test_sin_sesion_401(api: TestClient) -> None:
    assert api.get(URL).status_code == 401
    assert api.get(f"{URL}/PRO-000001__FIN-000001").status_code == 401


def test_el_lider_usa_productores_y_fincas_no_la_consulta(api: TestClient, lider: Cuenta) -> None:
    for ruta in (URL, f"{URL}/PRO-000001__FIN-000001"):
        r = api.get(ruta, headers=lider.headers)
        assert r.status_code == 403 and r.json()["codigo"] == "SIN_PERMISO"


# --- qué ve cada técnico -----------------------------------------------------------------------------------------


def test_el_tecnico_solo_recibe_productores_y_fincas_de_sus_casos(api: TestClient, mundo: dict[str, Any]) -> None:
    uno = registros(api, mundo["t1"])
    assert [(r["productor"]["nombre"], r["finca"]["nombre"]) for r in uno] == [
        ("Álvaro Díaz", "El Roble"),
        ("Ana Pérez", "La Esperanza"),
    ]
    dos = registros(api, mundo["t2"])
    assert [(r["productor"]["nombre"], r["finca"]["nombre"]) for r in dos] == [("Bea Núñez", "La Colina")]


def test_no_hay_filtracion_de_productores_ajenos_en_ninguna_parte(api: TestClient, mundo: dict[str, Any]) -> None:
    cuerpo = api.get(URL, headers=mundo["t2"].headers).text
    for ajeno in (
        "Ana",
        "Álvaro",
        "Esperanza",
        "Roble",
        mundo["ana"]["id"],
        mundo["alvaro"]["id"],
        "3001112001",
        "3001112002",
    ):
        assert ajeno not in cuerpo


def test_un_caso_reasignado_deja_de_dar_acceso(api: TestClient, mundo: dict[str, Any]) -> None:
    """La técnica 1 tuvo un caso de Bea, pero su asignación ya no está vigente."""
    assert all(r["productor"]["id"] != mundo["bea"]["id"] for r in registros(api, mundo["t1"]))
    r = api.get(f"{URL}/{mundo['bea']['id']}__{mundo['colina']['id']}", headers=mundo["t1"].headers)
    assert r.status_code == 404


def test_un_tecnico_sin_casos_no_ve_nada(api: TestClient, mundo: dict[str, Any], nueva_cuenta: NuevaCuenta) -> None:
    libre = nueva_cuenta(Rol.TECNICO, 3, nombre="Sin casos")
    r = api.get(URL, headers=libre.headers).json()
    assert r["items"] == [] and r["total"] == 0 and r["resumen"] == {"productores": 0, "fincas": 0, "casos": 0}


def test_un_caso_sin_tecnico_no_da_acceso_a_nadie(
    api: TestClient, lider: Cuenta, catalogos: None, nueva_cuenta: NuevaCuenta, migrated: Engine
) -> None:
    p = crear_productor(api, lider)
    finca = crear_finca(api, lider, p["id"])
    cultivo = crear_cultivo(api, lider, finca["id"])["cultivos"][0]["id"]
    crear_caso(migrated, cultivo, "GULUPA", "Pendiente de revisión")  # en la bandeja, sin técnico
    ana = nueva_cuenta(Rol.TECNICO)
    assert registros(api, ana) == []


# --- forma del registro ------------------------------------------------------------------------------------------


def test_el_registro_trae_productor_finca_cultivos_y_casos(api: TestClient, mundo: dict[str, Any]) -> None:
    ana = next(r for r in registros(api, mundo["t1"]) if r["productor"]["nombre"] == "Ana Pérez")
    assert ana["id"] == f"{mundo['ana']['id']}__{mundo['esperanza']['id']}"
    assert ana["productor"] == {"id": mundo["ana"]["id"], "nombre": "Ana Pérez", "celular": "3001112001"}
    assert ana["finca"] == {
        "id": mundo["esperanza"]["id"], "nombre": "La Esperanza", "vereda": "CENTRO", "latitud": 5.25, "longitud": -73.17,
    }  # fmt: skip
    assert set(ana) == {"id", "productor", "finca", "cultivos", "casos"}


def test_solo_aparecen_los_cultivos_de_sus_casos(api: TestClient, mundo: dict[str, Any]) -> None:
    ana = next(r for r in registros(api, mundo["t1"]) if r["productor"]["nombre"] == "Ana Pérez")
    assert [c["nombre"] for c in ana["cultivos"]] == ["Lote 1", "Lote 2"]  # «Lote sin casos» no aparece
    assert ana["cultivos"][0]["especies"] == ["GULUPA", "MARACUYA"]
    assert all(c["id"] != mundo["c_vacio"] for c in ana["cultivos"])


def test_los_casos_van_del_mas_reciente_al_mas_antiguo_con_su_codigo(api: TestClient, mundo: dict[str, Any]) -> None:
    ana = next(r for r in registros(api, mundo["t1"]) if r["productor"]["nombre"] == "Ana Pérez")
    ids = [c["id"] for c in ana["casos"]]
    assert ids == sorted(ids, reverse=True) and len(ids) == 3
    assert all(c["codigo"].startswith("#") and len(c["codigo"]) >= 5 for c in ana["casos"])
    assert {c["estado"] for c in ana["casos"]} == {"EN_REVISION", "REGISTRADO", "CERRADO"}


def test_dos_cultivos_de_la_misma_finca_son_un_solo_registro(api: TestClient, mundo: dict[str, Any]) -> None:
    assert sum(r["finca"]["nombre"] == "La Esperanza" for r in registros(api, mundo["t1"])) == 1


# --- filtros -----------------------------------------------------------------------------------------------------


def nombres(api: TestClient, tecnico: Cuenta, **filtros: Any) -> list[str]:
    return [r["productor"]["nombre"] for r in registros(api, tecnico, **filtros)]


def test_busca_por_productor_o_finca_sin_tildes(api: TestClient, mundo: dict[str, Any]) -> None:
    t1 = mundo["t1"]
    assert nombres(api, t1, q="alvaro") == ["Álvaro Díaz"]
    assert nombres(api, t1, q="ESPERANZA") == ["Ana Pérez"]
    assert nombres(api, t1, q="roble") == ["Álvaro Díaz"]
    assert nombres(api, t1, q="zzz") == []


def test_busca_por_el_numero_del_caso(api: TestClient, mundo: dict[str, Any]) -> None:
    ana_1 = int(mundo["casos"]["ana_1"].split("-")[1])
    assert nombres(api, mundo["t1"], q=f"#{ana_1:04d}") == ["Ana Pérez"]
    assert nombres(api, mundo["t1"], q=str(ana_1)) == ["Ana Pérez"]
    assert nombres(api, mundo["t2"], q=f"#{ana_1:04d}") == []  # el caso de otra técnica no se encuentra


def test_no_encuentra_productores_ajenos_ni_buscandolos_por_nombre(api: TestClient, mundo: dict[str, Any]) -> None:
    assert nombres(api, mundo["t2"], q="ana") == []
    assert nombres(api, mundo["t2"], q="3001112001") == []


def test_la_busqueda_no_es_inyectable(api: TestClient, mundo: dict[str, Any]) -> None:
    for q in ("'; DROP TABLE casos; --", "%", "_", "\\", "#"):
        assert api.get(URL, params={"q": q}, headers=mundo["t1"].headers).status_code == 200
    assert len(registros(api, mundo["t1"])) == 2


def test_filtra_por_vereda(api: TestClient, mundo: dict[str, Any]) -> None:
    assert nombres(api, mundo["t1"], vereda="CENTRO") == ["Ana Pérez"]
    assert nombres(api, mundo["t1"], vereda="SAN_LUIS") == ["Álvaro Díaz"]
    assert nombres(api, mundo["t1"], vereda="NO_EXISTE") == []


def test_filtra_por_especie(api: TestClient, mundo: dict[str, Any]) -> None:
    assert nombres(api, mundo["t1"], especie="GRANADILLA") == ["Ana Pérez"]
    assert nombres(api, mundo["t1"], especie="MARACUYA") == ["Álvaro Díaz", "Ana Pérez"]
    assert nombres(api, mundo["t2"], especie="MARACUYA") == []


def test_filtra_por_estado_de_los_casos(api: TestClient, mundo: dict[str, Any]) -> None:
    assert nombres(api, mundo["t1"], estado_casos="pendientes") == ["Ana Pérez"]  # tiene casos abiertos
    assert nombres(api, mundo["t1"], estado_casos="cerrados") == ["Álvaro Díaz"]  # todos terminados


def test_combina_filtros(api: TestClient, mundo: dict[str, Any]) -> None:
    assert nombres(api, mundo["t1"], vereda="CENTRO", especie="GULUPA", estado_casos="pendientes", q="ana") == [
        "Ana Pérez"
    ]
    assert nombres(api, mundo["t1"], vereda="SAN_LUIS", estado_casos="pendientes") == []


def test_el_resumen_cuenta_todo_lo_suyo_sin_importar_los_filtros(api: TestClient, mundo: dict[str, Any]) -> None:
    r = api.get(URL, params={"vereda": "SAN_LUIS"}, headers=mundo["t1"].headers).json()
    assert r["total"] == 1
    assert r["resumen"] == {"productores": 2, "fincas": 2, "casos": 4}
    assert api.get(URL, headers=mundo["t2"].headers).json()["resumen"] == {"productores": 1, "fincas": 1, "casos": 1}


def test_pagina(api: TestClient, mundo: dict[str, Any]) -> None:
    p1 = api.get(URL, params={"page": 1, "page_size": 1}, headers=mundo["t1"].headers).json()
    p2 = api.get(URL, params={"page": 2, "page_size": 1}, headers=mundo["t1"].headers).json()
    p3 = api.get(URL, params={"page": 3, "page_size": 1}, headers=mundo["t1"].headers).json()
    assert (p1["total"], p2["total"], p3["total"]) == (2, 2, 2)
    assert [len(p["items"]) for p in (p1, p2, p3)] == [1, 1, 0]
    assert p1["items"][0]["id"] != p2["items"][0]["id"]


@pytest.mark.parametrize(
    "parametros", [{"page": 0}, {"page_size": 101}, {"especie": "CURUBA"}, {"estado_casos": "todos"}, {"q": "x" * 101}]
)
def test_filtros_invalidos_422(api: TestClient, mundo: dict[str, Any], parametros: dict[str, object]) -> None:
    assert api.get(URL, params=parametros, headers=mundo["t1"].headers).status_code == 422


# --- detalle -----------------------------------------------------------------------------------------------------


def test_el_detalle_de_un_registro_propio(api: TestClient, mundo: dict[str, Any]) -> None:
    id_registro = f"{mundo['alvaro']['id']}__{mundo['roble']['id']}"
    r = api.get(f"{URL}/{id_registro}", headers=mundo["t1"].headers)
    assert r.status_code == 200 and r.json()["id"] == id_registro
    assert r.json()["finca"]["nombre"] == "El Roble" and [c["estado"] for c in r.json()["casos"]] == ["CERRADO"]


def test_el_detalle_de_un_registro_ajeno_no_existe(api: TestClient, mundo: dict[str, Any]) -> None:
    ajeno = f"{mundo['bea']['id']}__{mundo['colina']['id']}"
    r = api.get(f"{URL}/{ajeno}", headers=mundo["t1"].headers)
    assert r.status_code == 404 and r.json()["codigo"] == "NO_ENCONTRADO"
    assert api.get(f"{URL}/{ajeno}", headers=mundo["t2"].headers).status_code == 200


@pytest.mark.parametrize(
    "id_registro", ["PRO-000001", "x", "__", "PRO-000001__", "__FIN-000001", "PRO-999999__FIN-999999"]
)
def test_ids_mal_formados_o_inexistentes_404(api: TestClient, mundo: dict[str, Any], id_registro: str) -> None:
    assert api.get(f"{URL}/{id_registro}", headers=mundo["t1"].headers).status_code == 404


def test_productor_y_finca_que_no_se_corresponden_404(api: TestClient, mundo: dict[str, Any]) -> None:
    """Mezclar el productor propio con la finca de otro no abre nada."""
    mezcla = f"{mundo['ana']['id']}__{mundo['roble']['id']}"
    assert api.get(f"{URL}/{mezcla}", headers=mundo["t1"].headers).status_code == 404
