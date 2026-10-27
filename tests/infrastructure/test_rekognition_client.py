"""Moderación y dominio con Rekognition: se prueba con un cliente falso, sin salir a AWS."""

from typing import Any

import pytest

from app.infrastructure.rekognition_client import (
    ErrorModeracion,
    ModeradorSimulado,
    RekognitionReal,
    ResultadoModeracion,
)


class RekognitionFalso:
    def __init__(self, moderacion: list[str], etiquetas: list[str], error: Exception | None = None) -> None:
        self.moderacion, self.etiquetas, self.error = moderacion, etiquetas, error
        self.llamadas: list[tuple[str, dict[str, Any]]] = []

    def detect_moderation_labels(self, **p: Any) -> dict[str, Any]:
        self.llamadas.append(("moderacion", p))
        if self.error:
            raise self.error
        return {"ModerationLabels": [{"Name": n} for n in self.moderacion]}

    def detect_labels(self, **p: Any) -> dict[str, Any]:
        self.llamadas.append(("etiquetas", p))
        return {"Labels": [{"Name": n} for n in self.etiquetas]}


def test_una_planta_sin_contenido_inapropiado_es_valida() -> None:
    falso = RekognitionFalso([], ["Plant", "Leaf", "Outdoors"])
    resultado = RekognitionReal(lambda: falso, 70.0).analizar(b"bytes")
    assert resultado == ResultadoModeracion(inapropiada=False, es_planta=True)
    assert falso.llamadas[0][1] == {"Image": {"Bytes": b"bytes"}, "MinConfidence": 70.0}  # el original, no el WebP


def test_contenido_inapropiado_se_marca() -> None:
    resultado = RekognitionReal(lambda: RekognitionFalso(["Explicit Nudity"], ["Person"]), 70.0).analizar(b"x")
    assert resultado.inapropiada and not resultado.es_planta


def test_lo_que_no_es_una_planta_no_pasa_el_dominio() -> None:
    assert not RekognitionReal(lambda: RekognitionFalso([], ["Car", "Road"]), 70.0).analizar(b"x").es_planta


def test_las_etiquetas_se_comparan_sin_importar_mayusculas() -> None:
    assert RekognitionReal(lambda: RekognitionFalso([], ["FRUIT"]), 70.0).analizar(b"x").es_planta


def test_si_rekognition_falla_se_lanza_error_para_que_se_reintente() -> None:
    with pytest.raises(ErrorModeracion):
        RekognitionReal(lambda: RekognitionFalso([], [], error=TimeoutError()), 70.0).analizar(b"x")


def test_el_simulado_acepta_todo_salvo_lo_programado() -> None:
    m = ModeradorSimulado(inapropiadas={b"mala"}, no_plantas={b"auto"})
    assert m.analizar(b"hoja") == ResultadoModeracion(False, True)
    assert m.analizar(b"mala").inapropiada and not m.analizar(b"auto").es_planta and m.analizadas == 3
