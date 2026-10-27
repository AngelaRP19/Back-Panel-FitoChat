"""Cliente de IA simulado: lo que usa el chatbot mientras no existe la IA real (módulo 11)."""

from collections import deque

import pytest

from app.core import config
from app.infrastructure import ai_client
from app.infrastructure.ai_client import AiSimulado, ContextoConsulta, Turno, get_ai_client

CONTEXTO = ContextoConsulta("GULUPA", "Lote 1", "Todo el cultivo", (), None)


def test_sin_turnos_programados_da_por_completo_el_cuestionario() -> None:
    """No inventa preguntas del dominio: salta directo a las fotos."""
    assert AiSimulado().conducir(CONTEXTO, None) == Turno(completo=True)


def test_devuelve_los_turnos_programados_en_orden_y_anota_las_llamadas() -> None:
    ia = AiSimulado(turnos=deque([Turno(pregunta="¿Uno?"), Turno(pregunta="¿Dos?")]))
    assert ia.conducir(CONTEXTO, None).pregunta == "¿Uno?"
    assert ia.conducir(CONTEXTO, "respuesta").pregunta == "¿Dos?"
    assert ia.conducir(CONTEXTO, "otra").completo  # se acabaron
    assert [m for _, m in ia.llamadas] == [None, "respuesta", "otra"]


def test_por_defecto_un_turno_es_relevante_y_del_dominio() -> None:
    turno = Turno(pregunta="¿Dónde?")
    assert (turno.relevante, turno.fuera_de_dominio, turno.completo) == (True, False, False)


def test_en_modo_simulado_se_entrega_el_cliente_simulado() -> None:
    assert isinstance(get_ai_client(), AiSimulado)


def test_la_ia_real_llega_en_el_modulo_11(monkeypatch: pytest.MonkeyPatch) -> None:
    real = config.get_settings().model_copy(update={"integracion_ia": "real"})
    monkeypatch.setattr(ai_client, "get_settings", lambda: real)
    with pytest.raises(RuntimeError, match="módulo 11"):
        get_ai_client()
