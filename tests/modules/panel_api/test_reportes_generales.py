"""Reportes R1, R2, R4, R5 y resumen (B-08) como funciones puras sobre una foto de datos armada a mano."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from app.api.errors import ApiError
from app.modules.panel_api.reportes import generales as g
from app.modules.panel_api.reportes.datos import CasoR, CuentaR, Datos, FincaR, ProductorR
from app.modules.panel_api.reportes.schemas import KpiReporte, ReporteDatos, SeccionBarras, SeccionTabla

AHORA = datetime(2026, 10, 27, 17, 0, tzinfo=UTC)  # 12:00 en Bogotá
HOY = date(2026, 10, 27)


def hace(dias: float) -> datetime:
    return AHORA - timedelta(days=dias)


def caso(numero: int, **cambios: Any) -> CasoR:
    datos: dict[str, Any] = {
        "id": f"CAS-{numero:08d}",
        "codigo": f"#{numero:04d}",
        "estado": "PENDIENTE_REVISION",
        "creado_en": hace(5),
        "enviado_en": hace(5),
        "cerrado_en": None,
        "vereda": "CENTRO",
        "vereda_nombre": "Centro",
        "finca_id": "FIN-000001",
        "cultivo_id": "CUL-000001",
        "tipo_cultivo": "CAMPO_ABIERTO",
        "especie": "GULUPA",
        "productor_id": "PRO-000001",
        "productor_nombre": "Ana Pérez",
        "tecnico_id": None,
        "tecnico_nombre": None,
        "asignado_en": None,
        "preliminar": "PRELIMINAR",
        "categoria_preliminar": "TRIPS",
        "porcentaje": 80.0,
        "resultado": None,
        "diagnosticado_en": None,
    }
    return CasoR(**{**datos, **cambios})


ANA = CuentaR("TEC-000001", "Ana Técnica", "TECNICO", True)
BETO = CuentaR("TEC-000002", "Beto Técnico", "TECNICO", False)
FINCA_ANA = FincaR("FIN-000001", "La Esperanza", "CENTRO", "Centro", "PRO-000001", [("CAMPO_ABIERTO", ["GULUPA"])])
FINCA_ALVARO = FincaR("FIN-000002", "El Roble", "SAN_LUIS", "San Luis", "PRO-000002", [("INVERNADERO", ["MARACUYA"])])
PRODUCTORES = [
    ProductorR("PRO-000001", "Ana Pérez", True, date(2026, 1, 10), hace(3), [FINCA_ANA]),
    ProductorR("PRO-000002", "Álvaro Díaz", False, date(2026, 6, 1), None, [FINCA_ALVARO]),
]
CASOS = [
    caso(1),  # pendiente sin técnico
    caso(
        2,
        estado="EN_REVISION",
        tecnico_id=ANA.id,
        tecnico_nombre=ANA.nombre,
        asignado_en=hace(4),
        enviado_en=hace(10),
        creado_en=hace(10),
    ),
    caso(
        3,
        estado="CERRADO",
        tecnico_id=ANA.id,
        tecnico_nombre=ANA.nombre,
        asignado_en=hace(19),
        enviado_en=hace(20),
        creado_en=hace(20),
        diagnosticado_en=hace(18),
        cerrado_en=hace(17),
        resultado="CONFIRMADO",
        porcentaje=65.0,
    ),
    caso(
        4,
        estado="REGISTRADO",
        tecnico_id=BETO.id,
        tecnico_nombre=BETO.nombre,
        asignado_en=hace(2),
        diagnosticado_en=hace(1),
        resultado="OTRA_PATOLOGIA",
        categoria_preliminar="BOTRYTIS",
        vereda="SAN_LUIS",
        vereda_nombre="San Luis",
        especie="MARACUYA",
        productor_id="PRO-000002",
        productor_nombre="Álvaro Díaz",
        tipo_cultivo="INVERNADERO",
    ),
    caso(
        5,
        estado="DESCARTADO",
        enviado_en=None,
        creado_en=hace(1),
        preliminar=None,
        categoria_preliminar=None,
        porcentaje=None,
    ),
    caso(
        6,
        preliminar="NO_IDENTIFICADO",
        categoria_preliminar=None,
        porcentaje=None,
        vereda="LA_NUEVA",
        vereda_nombre="La Nueva",
    ),
]
DATOS = Datos(CASOS, PRODUCTORES, [ANA, BETO])
VACIO = Datos([], [], [])


def kpi(r: Any, rotulo: str) -> KpiReporte:
    return next(k for k in r.kpis if k.rotulo == rotulo)


def barras(r: ReporteDatos, id_: str) -> dict[str, float]:
    s = next(s for s in r.secciones if s.id == id_)
    assert isinstance(s, SeccionBarras)
    return {i.etiqueta: i.valor for i in s.items}


def tabla(r: Any, id_: str) -> list[dict[str, Any]]:
    s = next(s for s in r.secciones if s.id == id_)
    assert isinstance(s, SeccionTabla)
    return s.filas


# --- periodo -----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filtros", "esperado"),
    [
        ({}, (date(2026, 1, 1), HOY)),
        ({"periodo": "7d"}, (date(2026, 10, 21), HOY)),
        ({"periodo": "30d"}, (date(2026, 9, 28), HOY)),
        ({"periodo": "mes"}, (date(2026, 10, 1), HOY)),
        ({"desde": "2026-03-01"}, (date(2026, 3, 1), HOY)),
        (
            {"periodo": "personalizado", "desde": "2025-11-01", "hasta": "2026-10-31"},
            (date(2025, 11, 1), date(2026, 10, 31)),
        ),
    ],
)
def test_rango_del_periodo(filtros: dict[str, str], esperado: tuple[date, date]) -> None:
    assert g.rango(filtros, HOY) == esperado


@pytest.mark.parametrize(
    ("filtros", "campo"),
    [
        ({"desde": "2026-05-01", "hasta": "2026-04-01"}, "desde"),
        ({"desde": "2025-01-01", "hasta": "2026-06-01"}, "hasta"),
        ({"desde": "ayer"}, "desde"),
        ({"periodo": "siglo"}, "periodo"),
    ],
)
def test_periodo_invalido_422(filtros: dict[str, str], campo: str) -> None:
    with pytest.raises(ApiError) as error:
        g.rango(filtros, HOY)
    assert error.value.status_code == 422 and campo in (error.value.campos or {})


@pytest.mark.parametrize("tipo", list(g.GENERADORES))
def test_cada_reporte_funciona_sin_datos(tipo: str) -> None:
    r = g.GENERADORES[tipo](VACIO, {}, AHORA)
    assert r.tipo == tipo and r.total == 0 and len(r.kpis) == 5 and r.secciones


@pytest.mark.parametrize("tipo", list(g.GENERADORES))
def test_cada_reporte_se_serializa_con_alias(tipo: str) -> None:
    r = g.GENERADORES[tipo](DATOS, {}, AHORA).model_dump(by_alias=True, exclude_none=True)
    principal = next(s for s in r["secciones"] if s["id"] == "principal")
    assert "porPagina" in principal


# --- R1 ------------------------------------------------------------------------------------------------------------------


def test_r1_cuenta_estados_tiempos_y_veredas() -> None:
    r = g.reporte_casos(DATOS, {"periodo": "30d"}, AHORA)
    assert r.total == 6 and r.sustantivo == "casos"
    assert kpi(r, "Recibidos").valor == "6" and kpi(r, "Recibidos").pie == "5 enviados al panel"
    assert kpi(r, "Pendientes de revisión").valor == "2"
    assert kpi(r, "Cerrados").pie == "1 descartado por el chatbot"
    assert kpi(r, "Días hasta el cierre").valor == "3,0"
    estados = barras(r, "estados")
    assert estados["Pendiente de revisión"] == 2 and estados["Descartado"] == 1 and estados["Caso creado"] == 0
    veredas = tabla(r, "vereda-especie")
    assert [f["vereda"] for f in veredas] == ["Juracambita", "Centro", "San Luis", "La Nueva", "Total"]
    assert veredas[-1] == {"vereda": "Total", "GULUPA": 5, "GRANADILLA": 0, "MARACUYA": 1, "total": 6}
    principal = tabla(r, "principal")
    assert [f["caso"] for f in principal] == ["#0006", "#0005", "#0004", "#0003", "#0002", "#0001"]
    assert principal[-1]["_caso"] == "CAS-00000001" and principal[-1]["tecnico"] == "Sin técnico"
    assert "#0001 a #0006" in r.subtitulo


def test_r1_antiguedad_de_los_activos() -> None:
    r = g.reporte_casos(DATOS, {"periodo": "30d"}, AHORA)
    assert barras(r, "antiguedad") == {"0 a 1 día": 0, "2 a 3 días": 0, "4 a 7 días": 3, "Más de 7 días": 1}


@pytest.mark.parametrize(
    ("filtros", "codigos"),
    [
        ({"vereda": "SAN_LUIS"}, ["#0004"]),
        ({"especie": "MARACUYA"}, ["#0004"]),
        ({"tipo_cultivo": "INVERNADERO"}, ["#0004"]),
        ({"estado": "CERRADO,REGISTRADO"}, ["#0004", "#0003"]),
        ({"categoria": "NO_IDENTIFICADA"}, ["#0006"]),
        ({"coincidencia_min": "70"}, ["#0004", "#0002", "#0001"]),
        ({"coincidencia_max": "70"}, ["#0003"]),
        ({"resultado": "SIN_DIAGNOSTICO"}, ["#0006", "#0005", "#0002", "#0001"]),
        ({"tecnico": "sin"}, ["#0006", "#0005", "#0001"]),
        ({"tecnico": "TEC-000001"}, ["#0003", "#0002"]),
        ({"productor": "PRO-000002,PRO-000099"}, ["#0004"]),
        ({"espera": "7"}, ["#0002"]),
    ],
)
def test_r1_filtros(filtros: dict[str, str], codigos: list[str]) -> None:
    r = g.reporte_casos(DATOS, {"periodo": "30d", **filtros}, AHORA)
    assert [f["caso"] for f in tabla(r, "principal")] == codigos


def test_r1_fuera_del_periodo_no_cuenta() -> None:
    assert g.reporte_casos(DATOS, {"periodo": "7d"}, AHORA).total == 4


# --- R2 ------------------------------------------------------------------------------------------------------------------


def test_r2_matriz_y_kpis() -> None:
    r = g.reporte_identificacion(DATOS, {"periodo": "30d"}, AHORA)
    assert kpi(r, "Con identificación preliminar").pie == "4 con categoría · 1 no identificada"
    assert kpi(r, "El técnico confirmó la categoría").valor == "50 %"
    assert kpi(r, "Otra patología").pie == "#0004"
    matriz = {f["preliminar"]: f for f in tabla(r, "matriz")}
    assert matriz["Trips"]["CONFIRMADO"] == 1 and matriz["Trips"]["SIN_DIAGNOSTICO"] == 2
    assert matriz["Botrytis"]["OTRA_PATOLOGIA"] == 1 and matriz["Roña"]["CONFIRMADO"] == "—"
    assert matriz["No identificada"]["SIN_DIAGNOSTICO"] == 1
    assert [f["caso"] for f in tabla(r, "principal")] == ["#0003"]
    assert barras(r, "rangos") == {"Menos de 60 %": 0, "60 a 69 %": 1, "70 a 79 %": 0, "80 % o más": 3}


# --- R4 ------------------------------------------------------------------------------------------------------------------


def test_r4_productores_y_chatbot() -> None:
    r = g.reporte_productores(DATOS, {"periodo": "30d"}, AHORA)
    assert r.total == 2 and kpi(r, "Productores").pie == "1 activos · 1 deshabilitados"
    assert barras(r, "chatbot") == {"Casos creados": 6, "Enviados al panel": 5, "Descartados por el chatbot": 1}
    assert barras(r, "especies") == {"Gulupa": 1, "Granadilla": 0, "Maracuyá": 1}
    filas = tabla(r, "principal")
    assert [(f["productor"], f["casos"]) for f in filas] == [("Ana Pérez", 5), ("Álvaro Díaz", 1)]


@pytest.mark.parametrize(
    ("filtros", "nombres"),
    [
        ({"estado_productor": "deshabilitado"}, ["Álvaro Díaz"]),
        ({"vereda": "CENTRO"}, ["Ana Pérez"]),
        ({"especie": "MARACUYA"}, ["Álvaro Díaz"]),
        ({"tipo_cultivo": "CAMPO_ABIERTO"}, ["Ana Pérez"]),
        ({"casos_desde": "2"}, ["Ana Pérez"]),
        ({"casos_hasta": "1"}, ["Álvaro Díaz"]),
        ({"con_activos": "1"}, ["Ana Pérez", "Álvaro Díaz"]),
        ({"chatbot": "30"}, ["Ana Pérez"]),
        ({"chatbot": "90"}, ["Álvaro Díaz"]),
        ({"chatbot": "abandono"}, ["Ana Pérez"]),
        ({"registrado_desde": "2026-05-01"}, ["Álvaro Díaz"]),
    ],
)
def test_r4_filtros(filtros: dict[str, str], nombres: list[str]) -> None:
    r = g.reporte_productores(DATOS, {"periodo": "30d", **filtros}, AHORA)
    assert [f["productor"] for f in tabla(r, "principal")] == nombres


# --- R5 ------------------------------------------------------------------------------------------------------------------


def test_r5_carga_por_tecnico() -> None:
    r = g.reporte_tecnicos(DATOS, {"periodo": "30d"}, AHORA)
    assert r.total == 2 and r.sustantivo == "cuentas"
    assert kpi(r, "Casos asignados").valor == "3" and kpi(r, "Casos asignados").pie == "de 5 enviados al panel"
    assert kpi(r, "Pendientes sin técnico").pie == "#0001 y #0006"
    assert kpi(r, "Registrados sin cerrar").pie == "#0004 · Beto Técnico"
    filas = {f["tecnico"]: f for f in tabla(r, "principal")}
    assert filas["Ana Técnica"] == {
        "tecnico": "Ana Técnica",
        "cuenta": "Activa",
        "asignados": 2,
        "revision": 1,
        "registrados": 0,
        "cerrados": 1,
    }
    assert "extensiones" not in str(r.model_dump()).lower()  # D-07


@pytest.mark.parametrize(
    ("filtros", "nombres"),
    [
        ({"tecnicos": "TEC-000002"}, ["Beto Técnico"]),
        ({"estado_cuenta": "activa"}, ["Ana Técnica"]),
        ({"estado_cuenta": "deshabilitada"}, ["Beto Técnico"]),
        ({"con_pendientes": "1"}, ["Ana Técnica"]),
        ({"dias_desde": "3"}, ["Beto Técnico"]),
        ({"dias_hasta": "3"}, ["Ana Técnica"]),
        ({"rol": "LIDER_TECNICO"}, []),
    ],
)
def test_r5_filtros(filtros: dict[str, str], nombres: list[str]) -> None:
    r = g.reporte_tecnicos(DATOS, {"periodo": "30d", **filtros}, AHORA)
    assert [f["tecnico"] for f in tabla(r, "principal")] == nombres


# --- resumen -------------------------------------------------------------------------------------------------------------


def test_resumen_con_las_cinco_tarjetas() -> None:
    alertas = ReporteDatos(
        tipo="alertas",
        titulo="Alertas",
        subtitulo="",
        total=2,
        sustantivo="alertas",
        kpis=[
            KpiReporte(rotulo="Sin decisión", valor="1", pie="", tono="adv"),
            KpiReporte(rotulo="Productores avisados", valor="7", pie="", tono="ok"),
        ],
        secciones=[
            SeccionBarras(
                id="estados",
                titulo="Estados",
                ancho="mitad",
                items=[g.barra("Generada", 1), g.barra("Comunicada", 1), g.barra("Descartada", 0)],
            )
        ],
    )
    r = g.resumen(DATOS, {"periodo": "30d"}, AHORA, alertas)
    assert r.total == 6 and [t.tipo for t in r.reportes] == [
        "casos",
        "identificacion",
        "alertas",
        "productores",
        "tecnicos",
    ]
    assert kpi(r, "Alertas del periodo").pie == "1 generada · 1 comunicada"
    assert kpi(r, "Confirmados").valor == "1"
    alertas_tarjeta = next(t for t in r.reportes if t.tipo == "alertas")
    assert alertas_tarjeta.destacados == ["2 en el periodo", "1 por revisar", "7 productores avisados"]
    carga = {f["tecnico"]: f for f in tabla(r, "carga")}
    assert carga["Sin asignar"]["asignados"] == 2
    semanas = next(s for s in r.secciones if s.id == "semanas")
    assert semanas.titulo == "Casos recibidos por semana"


def test_resumen_sin_datos() -> None:
    alertas = ReporteDatos(
        tipo="alertas", titulo="", subtitulo="", total=0, sustantivo="alertas", kpis=[], secciones=[]
    )
    r = g.resumen(VACIO, {}, AHORA, alertas)
    assert r.total == 0 and kpi(r, "Alertas del periodo").pie == "ninguna"


def test_casos_por_semana_empiezan_el_lunes() -> None:
    lunes = datetime(2026, 10, 26, 15, 0, tzinfo=UTC)
    items = g.por_semana([replace(CASOS[0], enviado_en=lunes), replace(CASOS[0], enviado_en=lunes + timedelta(days=6))])
    assert [(i.etiqueta, i.valor) for i in items] == [("Sem. del 26 oct", 2)]
