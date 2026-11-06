"""Reglas de la identificación preliminar: umbral de coincidencia (50 %) y margen de ambigüedad (15 puntos)."""

import pytest

from app.core.catalogos_api import ResultadoAnalisis as R
from app.modules.identification.reglas import ordenar, resultado_de

UMBRAL, MARGEN = 50, 15


@pytest.mark.parametrize(
    ("porcentajes", "esperado"),
    [
        ({}, R.NO_IDENTIFICADO),
        ({"RONA": 49.99, "TRIPS": 10}, R.NO_IDENTIFICADO),  # justo debajo del umbral
        ({"RONA": 50, "TRIPS": 10}, R.PRELIMINAR),  # justo en el umbral
        ({"RONA": 80, "TRIPS": 10}, R.PRELIMINAR),
        ({"RONA": 70, "TRIPS": 60}, R.AMBIGUO),  # dos sobre el umbral, 10 puntos de diferencia
        ({"RONA": 70, "TRIPS": 55.01}, R.AMBIGUO),  # 14,99 puntos: todavía ambiguo
        ({"RONA": 70, "TRIPS": 55}, R.PRELIMINAR),  # 15 puntos exactos: ya hay ganadora clara
        ({"RONA": 90, "TRIPS": 50}, R.PRELIMINAR),
        ({"RONA": 60, "TRIPS": 40}, R.PRELIMINAR),  # la segunda no llega al umbral: no compite
        ({"RONA": 50, "TRIPS": 50}, R.AMBIGUO),  # empate
        ({"RONA": 60, "TRIPS": 58, "FUSARIUM": 20}, R.AMBIGUO),
    ],
)
def test_resultado_segun_umbral_y_margen(porcentajes: dict[str, float], esperado: R) -> None:
    assert resultado_de(porcentajes, UMBRAL, MARGEN) == esperado


def test_los_umbrales_vienen_de_la_configuracion_no_del_codigo() -> None:
    assert resultado_de({"RONA": 40}, 30, MARGEN) == R.PRELIMINAR
    assert resultado_de({"RONA": 70, "TRIPS": 55}, UMBRAL, 20) == R.AMBIGUO


def test_ordena_de_mayor_a_menor_y_desempata_por_codigo() -> None:
    assert ordenar({"TRIPS": 60, "RONA": 60, "BOTRYTIS": 80}) == [("BOTRYTIS", 80), ("RONA", 60), ("TRIPS", 60)]
