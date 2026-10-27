"""Reporte de alertas sin base de datos: periodo, filtros, cifras y secciones."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.api.errors import ApiError
from app.modules.panel_api.alertas.repository import AlertaFila, ParametroFila
from app.modules.panel_api.reportes.alertas import construir, decimal, filtrar
from app.modules.panel_api.reportes.filtros import FiltrosAlertas, estados_de, rango_del_periodo
from app.modules.panel_api.reportes.schemas import SeccionBarras, SeccionDatos, SeccionTabla

HOY = date(2026, 10, 27)
VEREDAS = {"CENTRO": "Centro", "SAN_LUIS": "San Luis", "JURACAMBITA": "Juracambita"}


def alerta(id_: int, estado: str = "GENERADA", **cambios: Any) -> AlertaFila:
    base: dict[str, Any] = {
        "id": f"ALR-{id_:06d}",
        "estado": estado,
        "id_categoria": "CAT-1",
        "nombre_categoria": "Trips",
        "generada_en": datetime(2026, 10, 1, 15, 0, tzinfo=UTC),
        "comunicada_el": None,
        "num_minimo": 3,
        "distancia_km": Decimal("3.5"),
        "intervalo_dias": 30,
        "recomendacion": None,
        "mensaje_enviado": None,
        "id_lider_gestion": None,
        "nombre_lider_gestion": None,
        "fecha_confirmacion": None,
        "justificacion_descarte": None,
        "mensaje_correccion": None,
        "fecha_correccion": None,
        "id_lider_correccion": None,
        "nombre_lider_correccion": None,
        "num_casos": 3,
        "radio_km": 1.0,
        "vereda": "Centro",
        "notificados": 0,
        "fallidos": 0,
    }
    return AlertaFila(**{**base, **cambios})


def decidida(id_: int, estado: str, dias: float, lider: str = "LT-1", **cambios: Any) -> AlertaFila:
    generada = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
    return alerta(
        id_,
        estado,
        generada_en=generada,
        id_lider_gestion=lider,
        nombre_lider_gestion="Laura Líder",
        fecha_confirmacion=generada + timedelta(days=dias),
        **cambios,
    )


PARAMETROS = [
    ParametroFila("CAT-1", "Trips", True, 3, Decimal("3.5"), 30, datetime(2026, 9, 15, 14, 0, tzinfo=UTC)),
    ParametroFila("CAT-2", "Botrytis", False, None, None, None, None),
    ParametroFila("CAT-3", "Roña", False, None, None, None, None),
]


def reporte(alertas: list[AlertaFila], desde: date = date(2026, 10, 1), hasta: date = HOY) -> Any:
    return construir(alertas, PARAMETROS, {"TRIPS": ("LT-1", "Laura Líder")}, VEREDAS, desde, hasta)


def seccion(r: Any, id_: str) -> Any:
    return next(s for s in r.secciones if s.id == id_)


def kpi(r: Any, rotulo: str) -> Any:
    return next(k for k in r.kpis if k.rotulo == rotulo)


# --- periodo -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("periodo", "esperado"),
    [
        ("7d", (date(2026, 10, 21), HOY)),
        ("30d", (date(2026, 9, 28), HOY)),
        ("mes", (date(2026, 10, 1), HOY)),
        ("anio", (date(2026, 1, 1), HOY)),
    ],
)
def test_periodos_predefinidos(periodo: str, esperado: tuple[date, date]) -> None:
    desde, hasta, p = rango_del_periodo(FiltrosAlertas(periodo=periodo), HOY)  # type: ignore[arg-type]
    assert (desde, hasta, p) == (*esperado, periodo)


def test_sin_periodo_ni_fechas_es_el_anio_en_curso() -> None:
    assert rango_del_periodo(FiltrosAlertas(), HOY) == (date(2026, 1, 1), HOY, "anio")


def test_con_fechas_y_sin_periodo_es_personalizado() -> None:
    f = FiltrosAlertas(desde=date(2026, 3, 1), hasta=date(2026, 3, 31))
    assert rango_del_periodo(f, HOY) == (date(2026, 3, 1), date(2026, 3, 31), "personalizado")
    assert rango_del_periodo(FiltrosAlertas(desde=date(2026, 3, 1)), HOY)[1] == HOY
    assert rango_del_periodo(FiltrosAlertas(hasta=date(2026, 3, 1)), HOY)[0] == date(2026, 1, 1)


def test_un_periodo_predefinido_ignora_las_fechas() -> None:
    f = FiltrosAlertas(periodo="7d", desde=date(2020, 1, 1), hasta=date(2020, 2, 1))
    assert rango_del_periodo(f, HOY)[:2] == (date(2026, 10, 21), HOY)


def test_un_mismo_dia_es_un_periodo_valido() -> None:
    f = FiltrosAlertas(desde=HOY, hasta=HOY)
    assert rango_del_periodo(f, HOY)[:2] == (HOY, HOY)


def test_fechas_invertidas_es_error_de_validacion() -> None:
    with pytest.raises(ApiError) as error:
        rango_del_periodo(FiltrosAlertas(desde=date(2026, 5, 2), hasta=date(2026, 5, 1)), HOY)
    assert (
        error.value.status_code == 422 and error.value.codigo == "VALIDACION" and "desde" in (error.value.campos or {})
    )


def test_estados_separados_por_coma() -> None:
    assert estados_de("GENERADA, CONFIRMADA,") == ["GENERADA", "CONFIRMADA"]
    assert estados_de(None) == [] and estados_de("") == []
    with pytest.raises(ApiError, match="no existen") as error:
        estados_de("GENERADA,INVENTADO")
    assert error.value.status_code == 422 and "INVENTADO" in (error.value.campos or {})["estado_alerta"]


# --- filtros -------------------------------------------------------------------------------------------------------


def test_filtra_por_estado_y_vereda() -> None:
    a = [alerta(1, "GENERADA"), alerta(2, "CONFIRMADA", vereda="San Luis"), alerta(3, "DESCARTADA", vereda=None)]
    assert [x.id for x in filtrar(a, FiltrosAlertas(estados=["GENERADA", "DESCARTADA"]))] == [
        "ALR-000001",
        "ALR-000003",
    ]
    assert [x.id for x in filtrar(a, FiltrosAlertas(vereda="SAN_LUIS"))] == ["ALR-000002"]
    assert filtrar(a, FiltrosAlertas(vereda="juracambita")) == []  # una alerta sin vereda no coincide con ninguna


def test_filtra_por_quien_decidio() -> None:
    a = [alerta(1), decidida(2, "CONFIRMADA", 1, "LT-1"), decidida(3, "DESCARTADA", 1, "LT-2")]
    assert [x.id for x in filtrar(a, FiltrosAlertas(decidida="sin"))] == ["ALR-000001"]
    assert [x.id for x in filtrar(a, FiltrosAlertas(decidida="LT-2"))] == ["ALR-000003"]


def test_filtra_por_casos_y_comunicacion() -> None:
    a = [alerta(1, num_casos=3), alerta(2, "CONFIRMADA", num_casos=5), alerta(3, "COMUNICADA", num_casos=4)]
    assert [x.id for x in filtrar(a, FiltrosAlertas(casos_min=4))] == ["ALR-000002", "ALR-000003"]
    assert [x.id for x in filtrar(a, FiltrosAlertas(comunicada="si"))] == ["ALR-000003"]
    assert [x.id for x in filtrar(a, FiltrosAlertas(comunicada="sin_comunicar"))] == ["ALR-000002"]
    assert len(filtrar(a, FiltrosAlertas(casos_min=0))) == 3  # cero es un mínimo válido


def test_los_filtros_se_combinan() -> None:
    a = [alerta(1, "CONFIRMADA", num_casos=5), alerta(2, "CONFIRMADA", num_casos=2), alerta(3, "GENERADA", num_casos=5)]
    f = FiltrosAlertas(estados=["CONFIRMADA"], casos_min=3)
    assert [x.id for x in filtrar(a, f)] == ["ALR-000001"]


# --- cifras --------------------------------------------------------------------------------------------------------


def escenario() -> list[AlertaFila]:
    return [
        alerta(4),
        decidida(3, "DESCARTADA", 3, justificacion_descarte="Falsa alarma", vereda="San Luis", num_casos=4),
        decidida(2, "COMUNICADA", 1, notificados=5),
        decidida(1, "CONFIRMADA", 2, notificados=0),
    ]


def test_indicadores() -> None:
    r = reporte(escenario())
    assert r.total == 4 and r.sustantivo == "alertas" and r.tipo == "alertas"
    assert (kpi(r, "Alertas generadas").valor, kpi(r, "Alertas generadas").pie) == ("4", "4 en octubre")
    assert (kpi(r, "Sin decisión").valor, kpi(r, "Sin decisión").pie) == ("1", "1 generada · 0 en revisión")
    assert (kpi(r, "Confirmadas").valor, kpi(r, "Confirmadas").pie) == ("2", "1 ya comunicadas")
    assert kpi(r, "Días hasta decidir").valor == "2,0"  # (2 + 1 + 3) / 3
    assert kpi(r, "Productores avisados").valor == "5"


def test_el_subtitulo_dice_el_periodo_y_las_categorias_que_participan() -> None:
    assert reporte([]).subtitulo == ("01/10/2026 a 27/10/2026 · posibles concentraciones de casos confirmados de Trips")


def test_estados_y_embudo() -> None:
    r = reporte(escenario())
    estados = {i.etiqueta: i.valor for i in seccion(r, "estados").items}
    assert estados == {
        "Generada": 1,
        "En revisión": 0,
        "Confirmada": 1,
        "Comunicada": 1,
        "Descartada": 1,
        "Corregida": 0,
    }
    embudo = seccion(r, "embudo")
    assert [i.valor for i in embudo.items] == [4, 3, 2, 1]  # generadas, revisadas, confirmadas, comunicadas
    assert (
        "1 descartada" in embudo.nota
        and "ALR-003: falsa alarma" in embudo.nota
        and "1 sin revisar (ALR-004)" in embudo.nota
    )


def test_alertas_por_vereda_incluye_las_veredas_sin_alertas() -> None:
    tabla = seccion(reporte(escenario()), "veredas")
    assert isinstance(tabla, SeccionTabla)
    filas = {f["vereda"]: f for f in tabla.filas}
    assert set(filas) == {"Centro", "Juracambita", "San Luis"}
    assert filas["Centro"] == {"vereda": "Centro", "alertas": 3, "confirmadas": 2, "descartadas": 0, "casos": 9}
    assert filas["San Luis"] == {"vereda": "San Luis", "alertas": 1, "confirmadas": 0, "descartadas": 1, "casos": 4}
    assert filas["Juracambita"]["alertas"] == 0


def test_alertas_por_mes_en_orden_cronologico_y_con_hora_de_bogota() -> None:
    a = [
        alerta(1, generada_en=datetime(2026, 10, 5, 12, 0, tzinfo=UTC)),
        alerta(2, generada_en=datetime(2026, 9, 30, 12, 0, tzinfo=UTC)),
        alerta(3, generada_en=datetime(2026, 11, 1, 3, 0, tzinfo=UTC)),  # 31-oct 22:00 en Bogotá: cuenta como octubre
    ]
    items = seccion(reporte(a, date(2026, 9, 1)), "meses").items
    assert [(i.etiqueta, i.valor) for i in items] == [("septiembre 2026", 1), ("octubre 2026", 2)]


def test_detalle_de_alertas() -> None:
    tabla = seccion(reporte(escenario()), "principal")
    por_id = {f["_alerta"]: f for f in tabla.filas}
    assert tabla.por_pagina == 10 and tabla.sustantivo == "alertas"
    assert por_id["ALR-000001"] == {
        "_alerta": "ALR-000001",
        "alerta": "ALR-001",
        "detectada": "01/10/2026",
        "vereda": "Centro",
        "casos": 3,
        "estado": "Confirmada",
        "decidio": "Laura Líder",
        "respuesta": "2 días",
        "avisados": "0 · falta comunicar",  # confirmada y todavía sin comunicar
    }
    assert por_id["ALR-000002"]["avisados"] == 5 and por_id["ALR-000002"]["respuesta"] == "1 día"
    assert por_id["ALR-000004"]["decidio"] == "—" and por_id["ALR-000004"]["respuesta"] == "—"


def test_una_decision_el_mismo_dia_cuenta_como_un_dia() -> None:
    fila = seccion(reporte([decidida(1, "CONFIRMADA", 0.1)]), "principal").filas[0]
    assert fila["respuesta"] == "1 día"


def test_parametros_vigentes() -> None:
    datos = seccion(reporte([]), "parametros")
    assert isinstance(datos, SeccionDatos)
    assert {i.termino: i.valor for i in datos.items} == {
        "Categorías que participan": "Trips",
        "Mínimo de casos": "3 casos confirmados",
        "Distancia máxima": "3,5 km",
        "Intervalo": "30 días hacia atrás",
        "Última modificación": "15/09/2026 · Laura Líder",
    }
    assert datos.nota == "Botrytis, Roña no participan en las alertas."


def test_con_varias_categorias_cada_parametro_dice_de_cual_es() -> None:
    otras = [*PARAMETROS, ParametroFila("CAT-4", "Fusarium", True, 2, Decimal("1"), 15, None)]
    r = construir([], otras, {}, VEREDAS, date(2026, 10, 1), HOY)
    terminos = [i.termino for i in seccion(r, "parametros").items]
    assert "Categorías que participan" in terminos and "Mínimo de casos (Fusarium)" in terminos
    assert {i.termino: i.valor for i in seccion(r, "parametros").items}["Última modificación (Fusarium)"] == "—"


def test_sin_alertas_el_reporte_sigue_siendo_valido() -> None:
    r = reporte([])
    assert r.total == 0 and kpi(r, "Días hasta decidir").valor == "—"
    assert seccion(r, "principal").filas == [] and seccion(r, "principal").vacio
    assert seccion(r, "embudo").nota is None and seccion(r, "meses").items == []
    assert all(isinstance(s, SeccionBarras | SeccionTabla | SeccionDatos) for s in r.secciones)


def test_decimal_con_coma() -> None:
    assert decimal(1.25) == "1,2" and decimal(None) == "—" and decimal(10) == "10,0"
