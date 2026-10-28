"""GET /reportes/{casos,identificacion,productores,tecnicos}, /reportes/resumen y /reportes/consultas (B-08)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.core.roles import Rol
from app.infrastructure.consultas_store import ConsultasDesactivadas, get_consultas_store
from app.main import app
from tests.api.conftest import Mundo, NuevaCuenta
from tests.api.conftest_alertas import Zona, construir_zona

URL = "/api/v1/reportes"
CONSULTAS = f"{URL}/consultas"
GENERALES = ["casos", "identificacion", "productores", "tecnicos"]


@pytest.fixture
def zona(api: TestClient, mundo: Mundo, migrated: Engine) -> Zona:
    return construir_zona(api, mundo, migrated)


def pedir(api: TestClient, actor: Any, ruta: str, **filtros: Any) -> Any:
    r = api.get(ruta, params=filtros, headers=actor.headers)
    assert r.status_code == 200, r.text
    return r.json()


def guardar(api: TestClient, actor: Any, **cambios: Any) -> Any:
    cuerpo = {"nombre": "Casos del mes", "tipo": "casos", "filtros": {"periodo": "30d"}, "visibilidad": "equipo"}
    r = api.post(CONSULTAS, json={**cuerpo, **cambios}, headers=actor.headers)
    assert r.status_code == 201, r.text
    return r.json()


# --- permisos --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("ruta", [*GENERALES, "resumen", "consultas"])
def test_sin_sesion_401(api: TestClient, ruta: str) -> None:
    assert api.get(f"{URL}/{ruta}").status_code == 401


@pytest.mark.parametrize("ruta", [*GENERALES, "resumen", "consultas"])
def test_el_tecnico_no_ve_los_reportes(api: TestClient, mundo: Mundo, ruta: str) -> None:
    r = api.get(f"{URL}/{ruta}", headers=mundo.t1.headers)
    assert r.status_code == 403 and r.json()["codigo"] == "SIN_PERMISO"


def test_reporte_que_no_existe_404(api: TestClient, mundo: Mundo) -> None:
    r = api.get(f"{URL}/conservacion", headers=mundo.lider.headers)
    assert r.status_code == 404 and r.json()["codigo"] == "NO_ENCONTRADO"


@pytest.mark.parametrize("ruta", [*GENERALES, "resumen"])
@pytest.mark.parametrize(
    ("filtros", "campo"),
    [
        ({"desde": "2026-05-01", "hasta": "2026-04-01"}, "desde"),
        ({"desde": "2025-01-01", "hasta": "2026-06-01"}, "hasta"),
        ({"periodo": "siglo"}, "periodo"),
        ({"desde": "ayer"}, "desde"),
    ],
)
def test_periodo_invalido_422(api: TestClient, mundo: Mundo, ruta: str, filtros: dict[str, str], campo: str) -> None:
    r = api.get(f"{URL}/{ruta}", params=filtros, headers=mundo.lider.headers)
    assert r.status_code == 422 and r.json()["codigo"] == "VALIDACION" and campo in r.json()["campos"]


# --- contenido -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("tipo", GENERALES)
def test_sin_datos_cada_reporte_viene_completo(api: TestClient, mundo: Mundo, tipo: str) -> None:
    r = pedir(api, mundo.lider, f"{URL}/{tipo}")
    assert r["tipo"] == tipo and len(r["kpis"]) == 5 and r["secciones"]
    principal = next(s for s in r["secciones"] if s["id"] == "principal")
    assert "porPagina" in principal and "por_pagina" not in principal


def test_r1_con_los_casos_de_la_zona(api: TestClient, zona: Zona, mundo: Mundo) -> None:
    r = pedir(api, mundo.lider, f"{URL}/casos", periodo="30d")
    assert r["total"] == 3 and r["sustantivo"] == "casos"
    principal = next(s for s in r["secciones"] if s["id"] == "principal")
    assert sorted(f["_caso"] for f in principal["filas"]) == sorted(zona.casos)
    assert all(f["caso"].startswith("#") and f["estado"] == "Registrado" for f in principal["filas"])
    solo = pedir(api, mundo.lider, f"{URL}/casos", periodo="30d", tecnico=mundo.t2.id)
    assert solo["total"] == 1


def test_r2_cuenta_las_confirmaciones(api: TestClient, zona: Zona, mundo: Mundo) -> None:
    r = pedir(api, mundo.lider, f"{URL}/identificacion", periodo="30d", resultado="CONFIRMADO")
    assert r["total"] == 3


def test_r4_y_r5_con_los_datos_de_la_zona(api: TestClient, zona: Zona, mundo: Mundo) -> None:
    r4 = pedir(api, mundo.lider, f"{URL}/productores", periodo="30d", estado_productor="activo")
    assert r4["total"] == 4  # Ana, Álvaro, Cerca y Lejos (Diego está deshabilitado)
    r5 = pedir(api, mundo.lider, f"{URL}/tecnicos", periodo="30d")
    filas = {f["tecnico"]: f for f in next(s for s in r5["secciones"] if s["id"] == "principal")["filas"]}
    assert filas["Ana Técnica"]["registrados"] == 2 and filas["Beto Técnico"]["registrados"] == 1


def test_resumen_trae_las_cinco_tarjetas_y_las_alertas(api: TestClient, zona: Zona, mundo: Mundo) -> None:
    r = pedir(api, mundo.lider, f"{URL}/resumen", periodo="30d")
    assert r["total"] == 3 and [t["tipo"] for t in r["reportes"]] == [
        "casos",
        "identificacion",
        "alertas",
        "productores",
        "tecnicos",
    ]
    alertas = next(k for k in r["kpis"] if k["rotulo"] == "Alertas del periodo")
    assert alertas["valor"] == "1"


# --- consultas guardadas -------------------------------------------------------------------------------------------


def test_guardar_consulta_devuelve_el_contrato(api: TestClient, zona: Zona, mundo: Mundo) -> None:
    c = guardar(api, mundo.lider, envio={"frecuencia": "semanal"})
    assert set(c) == {
        "id",
        "nombre",
        "tipo",
        "descripcion",
        "filtros",
        "resultado",
        "visibilidad",
        "envio",
        "creada_por",
    }
    assert c["id"].startswith("CNS-") and c["envio"] is None  # D-21: se ignora
    assert c["creada_por"] == "Laura Líder" and c["descripcion"] == "Casos y estados"
    assert c["resultado"] == "3 casos"
    assert pedir(api, mundo.lider, CONSULTAS) == [c]


@pytest.mark.parametrize(
    ("tipo", "filtros", "resultado"),
    [
        ("alertas", {"periodo": "30d"}, "1 alerta"),
        ("productores", {"periodo": "30d", "estado_productor": "deshabilitado"}, "1 productor"),
        ("tecnicos", {"periodo": "30d", "rol": "TECNICO"}, "2 cuentas"),
    ],
)
def test_el_resultado_usa_el_sustantivo_del_reporte(
    api: TestClient, zona: Zona, mundo: Mundo, tipo: str, filtros: dict[str, str], resultado: str
) -> None:
    assert guardar(api, mundo.lider, tipo=tipo, filtros=filtros)["resultado"] == resultado


@pytest.mark.parametrize(
    ("cambios", "campo"),
    [({"nombre": "abc "}, "nombre"), ({"nombre": "x" * 61}, "nombre"), ({"tipo": "conservacion"}, "tipo")],
)
def test_guardar_consulta_invalida_422(api: TestClient, mundo: Mundo, cambios: dict[str, Any], campo: str) -> None:
    cuerpo = {"nombre": "Casos del mes", "tipo": "casos", "filtros": {}, "visibilidad": "equipo", **cambios}
    r = api.post(CONSULTAS, json=cuerpo, headers=mundo.lider.headers)
    assert r.status_code == 422 and campo in r.json()["campos"]


def test_las_privadas_de_otro_lider_no_se_ven(api: TestClient, mundo: Mundo, nueva_cuenta: NuevaCuenta) -> None:
    otro = nueva_cuenta(Rol.LIDER_TECNICO, 9, nombre="Otro Líder")
    privada = guardar(api, mundo.lider, nombre="Solo para mí", visibilidad="yo")
    publica = guardar(api, mundo.lider, nombre="Para el equipo")
    assert [c["id"] for c in pedir(api, mundo.lider, CONSULTAS)] == [publica["id"], privada["id"]]
    assert [c["id"] for c in pedir(api, otro, CONSULTAS)] == [publica["id"]]
    assert api.delete(f"{CONSULTAS}/{privada['id']}", headers=otro.headers).status_code == 404
    r = api.delete(f"{CONSULTAS}/{publica['id']}", headers=otro.headers)
    assert r.status_code == 403 and r.json()["codigo"] == "SIN_PERMISO"


def test_borrar_consulta_solo_el_autor(api: TestClient, mundo: Mundo) -> None:
    c = guardar(api, mundo.lider)
    assert api.delete(f"{CONSULTAS}/{c['id']}", headers=mundo.t1.headers).status_code == 403
    assert api.delete(f"{CONSULTAS}/{c['id']}", headers=mundo.lider.headers).status_code == 204
    assert pedir(api, mundo.lider, CONSULTAS) == []
    assert api.delete(f"{CONSULTAS}/{c['id']}", headers=mundo.lider.headers).status_code == 404


def test_sin_tabla_en_produccion_guardar_responde_503(api: TestClient, mundo: Mundo) -> None:
    app.dependency_overrides[get_consultas_store] = ConsultasDesactivadas
    try:
        cuerpo = {"nombre": "Casos del mes", "tipo": "casos", "filtros": {}, "visibilidad": "equipo"}
        r = api.post(CONSULTAS, json=cuerpo, headers=mundo.lider.headers)
        assert r.status_code == 503 and r.json()["codigo"] == "ERROR_INTERNO"
        assert pedir(api, mundo.lider, CONSULTAS) == []
    finally:
        app.dependency_overrides.pop(get_consultas_store, None)
