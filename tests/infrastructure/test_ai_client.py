"""Cliente de IA simulado: lo que usa el chatbot mientras no existe la IA real (módulo 11)."""

from collections import deque

import pytest

from app.core import config
from app.infrastructure import ai_client
from app.infrastructure.ai_client import (
    AiSimulado,
    AnalisisIA,
    ContextoConsulta,
    ErrorIA,
    Turno,
    con_reintentos,
    get_ai_client,
)

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


def test_la_ia_real_exige_los_modelos_de_bedrock(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.infrastructure import bedrock_client

    real = config.get_settings().model_copy(
        update={"integracion_ia": "real", "bedrock_model_text": None, "bedrock_model_vision": None}
    )
    monkeypatch.setattr(ai_client, "get_settings", lambda: real)
    monkeypatch.setattr(bedrock_client, "get_settings", lambda: real)
    with pytest.raises(RuntimeError, match="BEDROCK_MODEL"):
        get_ai_client()


def test_el_simulado_no_concluye_nada_a_menos_que_se_programe() -> None:
    ia = AiSimulado()
    assert ia.analizar(CONTEXTO, [], [], ["RONA"]) == AnalisisIA({}, {}, "")
    assert len(ia.embedding("x")) == ia.dimension
    ia.analisis = AnalisisIA({"RONA": 80.0})
    assert ia.analizar(CONTEXTO, [], [], ["RONA"]).porcentajes == {"RONA": 80.0}


def test_reintenta_y_devuelve_el_primer_exito() -> None:
    intentos = []

    def llamada() -> str:
        intentos.append(1)
        if len(intentos) < 3:
            raise ErrorIA("caída")
        return "ok"

    pausas: list[float] = []
    assert con_reintentos(llamada, 3, 0.5, pausas.append) == "ok"
    assert len(intentos) == 3 and pausas == [0.5, 0.5]


def test_si_todos_los_intentos_fallan_lanza_el_ultimo_error_sin_pausa_final() -> None:
    pausas: list[float] = []

    def llamada() -> str:
        raise ErrorIA("siempre")

    with pytest.raises(ErrorIA, match="siempre"):
        con_reintentos(llamada, 2, 1.0, pausas.append)
    assert pausas == [1.0]  # entre el primero y el segundo, no después del último


def test_con_cero_intentos_se_llama_una_vez() -> None:
    llamadas = []

    def llamada() -> str:
        llamadas.append(1)
        raise ErrorIA("x")

    with pytest.raises(ErrorIA):
        con_reintentos(llamada, 0, 0, lambda _: None)
    assert len(llamadas) == 1


def test_un_error_que_no_es_de_la_ia_no_se_reintenta() -> None:
    llamadas = []

    def llamada() -> str:
        llamadas.append(1)
        raise KeyError("bug")

    with pytest.raises(KeyError):
        con_reintentos(llamada, 3, 0, lambda _: None)
    assert len(llamadas) == 1
