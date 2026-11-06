"""Exportación del reporte de alertas a CSV y PDF, sin base de datos."""

from datetime import date

import pytest

from app.modules.panel_api.alertas.repository import AlertaFila
from app.modules.panel_api.reportes.alertas import construir
from app.modules.panel_api.reportes.exportar import a_csv, a_pdf, documento_de, mes_completo, sin_tildes
from app.modules.panel_api.reportes.schemas import Documento
from tests.modules.panel_api.test_reportes_alertas import PARAMETROS, VEREDAS, alerta, decidida

DESDE, HASTA = date(2026, 9, 1), date(2026, 9, 30)


def documento(
    alertas: list[AlertaFila] | None = None,
    desde: date = DESDE,
    hasta: date = HASTA,
    chips: list[str] | None = None,
) -> Documento:
    reporte = construir(alertas or [], PARAMETROS, {}, VEREDAS, desde, hasta)
    return documento_de(reporte, desde, hasta, chips or ["Vereda: todas", "Estados: todos"], "02/10/2026 a las 08:15")


def csv_texto(d: Documento) -> str:
    return a_csv(d).decode("utf-8")


# --- nombre del archivo y periodo ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("desde", "hasta", "completo"),
    [
        (date(2026, 9, 1), date(2026, 9, 30), True),
        (date(2026, 2, 1), date(2026, 2, 28), True),
        (date(2026, 9, 1), date(2026, 9, 28), True),  # desde el 28 se considera mes completo, como en el panel
        (date(2026, 9, 1), date(2026, 9, 27), False),
        (date(2026, 9, 2), date(2026, 9, 30), False),
        (date(2026, 9, 1), date(2026, 10, 31), False),
    ],
)
def test_mes_completo(desde: date, hasta: date, completo: bool) -> None:
    assert mes_completo(desde, hasta) is completo


def test_nombre_de_archivo_de_un_mes_y_de_un_rango() -> None:
    assert documento().nombre_archivo == "Reporte_alertas_septiembre_2026"
    assert documento().periodo_corto == "septiembre de 2026"
    d = documento(desde=date(2026, 1, 1), hasta=date(2026, 10, 27))
    assert (
        d.nombre_archivo == "Reporte_alertas_2026-01-01_a_2026-10-27" and d.periodo_corto == "01/01/2026 – 27/10/2026"
    )
    assert d.periodo_largo == "Periodo del 01/01/2026 al 27/10/2026"


def test_el_nombre_de_archivo_no_lleva_tildes() -> None:
    assert sin_tildes("Reporte_alertas_Año_ñandú") == "Reporte_alertas_Ano_nandu"


def test_los_filtros_aplicados_empiezan_por_el_periodo() -> None:
    assert documento().chips == ["Periodo: 01/09/2026 – 30/09/2026", "Vereda: todas", "Estados: todos"]


# --- CSV -------------------------------------------------------------------------------------------------------------


def test_el_csv_lleva_bom_y_separa_con_punto_y_coma() -> None:
    crudo = a_csv(documento())
    assert crudo.startswith("﻿".encode())
    texto = crudo.decode("utf-8-sig")
    assert texto.startswith("Reporte de alertas epidemiológicas\r\n")
    assert "Periodo;01/09/2026 – 30/09/2026" in texto and "Vereda;todas" in texto
    assert "Generado;02/10/2026 a las 08:15" in texto
    assert "Indicador;Valor;Detalle" in texto and "Alertas generadas;0;0 en septiembre" in texto
    assert "\r\n\r\n" in texto and texto.endswith("\r\n")


def test_el_csv_trae_cada_seccion_como_tabla() -> None:
    texto = csv_texto(
        documento(
            [decidida(1, "CONFIRMADA", 2, notificados=0), alerta(2, vereda="San Luis")], DESDE, date(2026, 10, 27)
        )
    )
    assert "Alerta;Detectada;Vereda;Casos;Estado;Decidió;Respuesta;Avisados" in texto
    assert "ALR-001;01/10/2026;Centro;3;Confirmada;Laura Líder;2 días;0 · falta comunicar" in texto
    assert "Alertas por estado (2 alertas)" in texto and "Dato;Valor" in texto
    assert "Parámetros vigentes" in texto and "Distancia máxima;3,5 km" in texto
    assert "Botrytis, Roña no participan en las alertas." in texto  # las notas también se exportan


def test_una_tabla_vacia_dice_que_no_hay_datos() -> None:
    assert "Ninguna alerta coincide con los filtros." in csv_texto(documento())


def test_el_csv_protege_las_celdas_con_separadores_y_comillas() -> None:
    a = decidida(1, "DESCARTADA", 1, justificacion_descarte='Falsa; "alarma"\nde prueba')
    texto = csv_texto(documento([a], DESDE, date(2026, 10, 27)))
    assert '"1 descartada (ALR-001: falsa; ""alarma""\nde prueba)"' in texto  # la nota entera, entre comillas


@pytest.mark.parametrize("peligroso", ["=SUMA(A1)", "+57300", "@cmd", "-HYPERLINK(x)"])
def test_el_csv_neutraliza_formulas(peligroso: str) -> None:
    d = documento(chips=[f"Vereda: {peligroso}"])
    assert f"Vereda;'{peligroso}" in csv_texto(d)


def test_el_csv_deja_pasar_numeros_negativos_y_guiones_solos() -> None:
    d = documento(chips=["Vereda: -3", "Estados: -"])
    texto = csv_texto(d)
    assert "Vereda;-3" in texto and "Estados;-" in texto


# --- PDF -------------------------------------------------------------------------------------------------------------


def test_el_pdf_es_un_pdf_valido_y_no_vacio() -> None:
    crudo = a_pdf(documento([decidida(1, "CONFIRMADA", 2), alerta(2)], DESDE, date(2026, 10, 27)))
    assert crudo.startswith(b"%PDF-") and crudo.rstrip().endswith(b"%%EOF") and len(crudo) > 2000


def test_el_pdf_de_un_reporte_vacio_tambien_se_genera() -> None:
    assert a_pdf(documento()).startswith(b"%PDF-")


def test_un_reporte_largo_ocupa_varias_paginas() -> None:
    muchas = [alerta(n, vereda="Centro") for n in range(1, 120)]
    crudo = a_pdf(documento(muchas, DESDE, date(2026, 10, 27)))
    assert crudo.count(b"/Type /Page\n") + crudo.count(b"/Type /Page ") >= 3


def test_el_pdf_soporta_tildes_y_caracteres_especiales() -> None:
    a = decidida(1, "DESCARTADA", 1, justificacion_descarte="Roña <b>en</b> & más — «prueba»")
    assert a_pdf(documento([a], DESDE, date(2026, 10, 27))).startswith(b"%PDF-")
