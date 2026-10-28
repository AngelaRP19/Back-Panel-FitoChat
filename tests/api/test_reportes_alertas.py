"""GET /reportes/alertas y /reportes/alertas/exportar: solo el líder, con filtros y exportación a CSV y PDF."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tests.api.conftest import Mundo
from tests.api.conftest_alertas import Zona, construir_zona

URL = "/api/v1/reportes/alertas"
EXPORTAR = f"{URL}/exportar"


@pytest.fixture
def zona(api: TestClient, mundo: Mundo, migrated: Engine) -> Zona:
    return construir_zona(api, mundo, migrated)


def pedir(api: TestClient, actor: Any, ruta: str = URL, **filtros: Any) -> Any:
    r = api.get(ruta, params=filtros, headers=actor.headers)
    assert r.status_code == 200, r.text
    return r.json()


def kpi(reporte: Any, rotulo: str) -> Any:
    return next(k for k in reporte["kpis"] if k["rotulo"] == rotulo)


def seccion(reporte: Any, id_: str) -> Any:
    return next(s for s in reporte["secciones"] if s["id"] == id_)


# --- permisos --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("ruta", [URL, EXPORTAR])
def test_sin_sesion_401(api: TestClient, ruta: str) -> None:
    assert api.get(ruta).status_code == 401


@pytest.mark.parametrize("ruta", [URL, EXPORTAR])
def test_el_tecnico_no_ve_los_reportes(api: TestClient, mundo: Mundo, ruta: str) -> None:
    r = api.get(ruta, headers=mundo.t1.headers)
    assert r.status_code == 403 and r.json()["codigo"] == "SIN_PERMISO"


def test_rango_mayor_a_12_meses_422(api: TestClient, mundo: Mundo) -> None:
    r = api.get(URL, params={"desde": "2025-01-01", "hasta": "2026-06-01"}, headers=mundo.lider.headers)
    assert r.status_code == 422 and r.json()["campos"] == {"hasta": "Máximo 12 meses."}


# --- contenido -------------------------------------------------------------------------------------------------------


def test_sin_alertas_el_reporte_viene_vacio_pero_completo(api: TestClient, mundo: Mundo) -> None:
    r = pedir(api, mundo.lider)
    assert r["tipo"] == "alertas" and r["total"] == 0 and r["sustantivo"] == "alertas"
    assert [k["rotulo"] for k in r["kpis"]] == [
        "Alertas generadas",
        "Sin decisión",
        "Confirmadas",
        "Días hasta decidir",
        "Productores avisados",
    ]
    assert [s["id"] for s in r["secciones"]] == ["estados", "embudo", "veredas", "meses", "principal", "parametros"]
    assert seccion(r, "principal")["filas"] == []


def test_una_alerta_generada_aparece_en_todo_el_reporte(api: TestClient, mundo: Mundo, zona: Zona) -> None:
    r = pedir(api, mundo.lider)
    assert r["total"] == 1 and kpi(r, "Sin decisión")["valor"] == "1" and kpi(r, "Días hasta decidir")["valor"] == "—"
    fila = seccion(r, "principal")["filas"][0]
    assert fila["_alerta"] == zona.alerta and fila["estado"] == "Generada" and fila["casos"] == 3
    assert fila["vereda"] == "Centro" and fila["decidio"] == "—"
    veredas = {f["vereda"]: f for f in seccion(r, "veredas")["filas"]}
    assert veredas["Centro"]["alertas"] == 1 and veredas["San Luis"]["alertas"] == 0
    assert seccion(r, "parametros")["items"][0] == {"termino": "Categorías que participan", "valor": "Trips"}
    assert seccion(r, "principal")["porPagina"] == 10  # el panel lee `porPagina`, no `por_pagina`


def test_las_respuestas_no_llevan_campos_nulos(api: TestClient, mundo: Mundo, zona: Zona) -> None:
    r = pedir(api, mundo.lider)
    assert all(None not in s.values() for s in r["secciones"])


def test_una_alerta_confirmada_cuenta_su_decision(api: TestClient, mundo: Mundo, zona: Zona, migrated: Engine) -> None:
    with migrated.begin() as c:
        c.execute(
            text(
                "UPDATE alertas SET estado_alerta = 'CONFIRMADA', id_lider_gestion = :l, "
                "fecha_confirmacion = fecha_generacion_alerta + interval '2 days' WHERE id_alerta = :a"
            ),
            {"l": mundo.lider.id, "a": zona.alerta},
        )
    r = pedir(api, mundo.lider)
    assert kpi(r, "Confirmadas")["valor"] == "1" and kpi(r, "Días hasta decidir")["valor"] == "2,0"
    fila = seccion(r, "principal")["filas"][0]
    assert (
        fila["decidio"] == "Laura Líder" and fila["respuesta"] == "2 días" and fila["avisados"] == "0 · falta comunicar"
    )


# --- filtros ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filtros", "total"),
    [
        ({}, 1),
        ({"periodo": "7d"}, 1),
        ({"periodo": "mes"}, 1),
        ({"periodo": "anio"}, 1),
        ({"periodo": "personalizado", "desde": "2020-01-01", "hasta": "2020-12-31"}, 0),
        ({"desde": "2020-01-01", "hasta": "2020-12-31"}, 0),
        ({"estado_alerta": "GENERADA"}, 1),
        ({"estado_alerta": "CONFIRMADA,COMUNICADA"}, 0),
        ({"vereda": "CENTRO"}, 1),
        ({"vereda": "SAN_LUIS"}, 0),
        ({"casos_min": 3}, 1),
        ({"casos_min": 4}, 0),
        ({"decidida": "sin"}, 1),
        ({"comunicada": "si"}, 0),
        ({"comunicada": "sin_comunicar"}, 0),
    ],
)
def test_filtros(api: TestClient, mundo: Mundo, zona: Zona, filtros: dict[str, Any], total: int) -> None:
    assert pedir(api, mundo.lider, **filtros)["total"] == total


@pytest.mark.parametrize(
    "filtros",
    [
        {"estado_alerta": "INVENTADO"},
        {"desde": "2026-05-02", "hasta": "2026-05-01"},
        {"desde": "ayer"},
        {"periodo": "siempre"},
        {"casos_min": -1},
        {"comunicada": "tal vez"},
    ],
)
def test_filtros_invalidos_422(api: TestClient, mundo: Mundo, filtros: dict[str, Any]) -> None:
    for ruta in (URL, EXPORTAR):
        r = api.get(ruta, params=filtros, headers=mundo.lider.headers)
        assert r.status_code == 422 and r.json()["codigo"] == "VALIDACION", (ruta, r.text)


def test_el_filtro_por_decisor(api: TestClient, mundo: Mundo, zona: Zona, migrated: Engine) -> None:
    with migrated.begin() as c:
        c.execute(
            text(
                "UPDATE alertas SET estado_alerta = 'DESCARTADA', id_lider_gestion = :l, fecha_confirmacion = now(), "
                "justificacion_descarte = 'No era Trips' WHERE id_alerta = :a"
            ),
            {"l": mundo.lider.id, "a": zona.alerta},
        )
    assert pedir(api, mundo.lider, decidida=mundo.lider.id)["total"] == 1
    assert pedir(api, mundo.lider, decidida="sin")["total"] == 0
    assert pedir(api, mundo.lider, decidida="LT-OTRO")["total"] == 0
    assert "ALR-" in seccion(pedir(api, mundo.lider), "embudo")["nota"]


def test_solo_cuentan_las_alertas_del_periodo(api: TestClient, mundo: Mundo, zona: Zona, migrated: Engine) -> None:
    with migrated.begin() as c:
        c.execute(
            text("UPDATE alertas SET fecha_generacion_alerta = '2025-03-10 15:00:00+00' WHERE id_alerta = :a"),
            {"a": zona.alerta},
        )
    assert pedir(api, mundo.lider)["total"] == 0  # el año en curso ya no la incluye
    r = pedir(api, mundo.lider, desde="2025-03-10", hasta="2025-03-10")  # justo ese día, ambos extremos incluidos
    assert r["total"] == 1 and seccion(r, "meses")["items"][0]["etiqueta"] == "marzo 2025"


# --- exportación -----------------------------------------------------------------------------------------------------


def test_exporta_csv_por_defecto(api: TestClient, mundo: Mundo, zona: Zona) -> None:
    r = api.get(EXPORTAR, headers=mundo.lider.headers)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"].startswith('attachment; filename="Reporte_alertas_')
    assert r.headers["content-disposition"].endswith('.csv"')
    texto = r.content.decode("utf-8-sig")
    assert texto.startswith("Reporte de alertas epidemiológicas") and "ALR-" in texto and "Generado;" in texto
    assert r.content.startswith("﻿".encode())


def test_exporta_pdf(api: TestClient, mundo: Mundo, zona: Zona) -> None:
    r = api.get(EXPORTAR, params={"formato": "pdf"}, headers=mundo.lider.headers)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].endswith('.pdf"') and r.content.startswith(b"%PDF-")


def test_la_exportacion_respeta_los_filtros_y_los_muestra(api: TestClient, mundo: Mundo, zona: Zona) -> None:
    r = api.get(
        EXPORTAR,
        params={
            "vereda": "CENTRO",
            "estado_alerta": "GENERADA",
            "casos_min": 2,
            "desde": "2026-01-01",
            "hasta": "2026-12-31",
        },
        headers=mundo.lider.headers,
    )
    texto = r.content.decode("utf-8-sig")
    assert "Vereda;Centro" in texto and "Estados;Generada" in texto and "Casos;al menos 2" in texto
    assert "Periodo;01/01/2026 – 31/12/2026" in texto
    assert 'filename="Reporte_alertas_2026-01-01_a_2026-12-31.csv"' in r.headers["content-disposition"]


def test_un_mes_completo_da_nombre_de_mes(api: TestClient, mundo: Mundo) -> None:
    r = api.get(EXPORTAR, params={"desde": "2026-09-01", "hasta": "2026-09-30"}, headers=mundo.lider.headers)
    assert 'filename="Reporte_alertas_septiembre_2026.csv"' in r.headers["content-disposition"]


def test_formato_desconocido_422(api: TestClient, mundo: Mundo) -> None:
    assert api.get(EXPORTAR, params={"formato": "xlsx"}, headers=mundo.lider.headers).status_code == 422


def test_exportar_no_modifica_nada(api: TestClient, mundo: Mundo, zona: Zona, migrated: Engine) -> None:
    def huella() -> list[Any]:
        with migrated.connect() as c:
            return [
                c.execute(text("SELECT count(*) FROM alertas")).scalar(),
                c.execute(text("SELECT count(*) FROM auditoria")).scalar(),
            ]

    antes = huella()
    pedir(api, mundo.lider)
    api.get(EXPORTAR, params={"formato": "pdf"}, headers=mundo.lider.headers)
    assert huella() == antes
