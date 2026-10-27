"""Cliente de Bedrock: arma el prompt, interpreta el JSON de la IA y la contiene si responde mal."""

import io
import json
from typing import Any

import pytest

from app.core.config import get_settings
from app.infrastructure import bedrock_client
from app.infrastructure.ai_client import ContextoConsulta, ErrorIA
from app.infrastructure.bedrock_client import BedrockAi, extraer_json

CONTEXTO = ContextoConsulta("GULUPA", "Lote 1", "Hojas bajas", (("¿Hace cuánto?", "Dos semanas"),), "¿Hay manchas?")
CATEGORIAS = ["BOTRYTIS", "RONA", "TRIPS"]


class RuntimeFalso:
    def __init__(self, texto: str = "", error: Exception | None = None, vector: list[float] | None = None) -> None:
        self.texto, self.error, self.vector = texto, error, vector
        self.converse_llamadas: list[dict[str, Any]] = []
        self.invoke_llamadas: list[dict[str, Any]] = []

    def converse(self, **parametros: Any) -> dict[str, Any]:
        self.converse_llamadas.append(parametros)
        if self.error:
            raise self.error
        return {"output": {"message": {"content": [{"text": self.texto}]}}}

    def invoke_model(self, **parametros: Any) -> dict[str, Any]:
        self.invoke_llamadas.append(parametros)
        if self.error:
            raise self.error
        return {"body": io.BytesIO(json.dumps({"embedding": self.vector}).encode())}


def _ia(runtime: RuntimeFalso, dimension: int = 3) -> BedrockAi:
    return BedrockAi(lambda: runtime, "modelo-texto", "modelo-vision", "modelo-embed", dimension)


def test_extraer_json_tolera_texto_alrededor() -> None:
    assert extraer_json('Claro:\n```json\n{"a": 1}\n```') == {"a": 1}


@pytest.mark.parametrize("texto", ["sin json", "{no es json}", "[1, 2]", "}{"])
def test_extraer_json_rechaza_lo_que_no_es_un_objeto(texto: str) -> None:
    with pytest.raises(ErrorIA):
        extraer_json(texto)


def test_conducir_interpreta_el_turno_y_usa_el_modelo_de_texto() -> None:
    runtime = RuntimeFalso('{"pregunta": " ¿Dónde ves las manchas? ", "completo": false, "relevante": true}')
    turno = _ia(runtime).conducir(CONTEXTO, "Sí, en las hojas")
    assert (turno.pregunta, turno.completo, turno.relevante, turno.fuera_de_dominio) == (
        "¿Dónde ves las manchas?",
        False,
        True,
        False,
    )
    llamada = runtime.converse_llamadas[0]
    entrada = llamada["messages"][0]["content"][0]["text"]
    assert llamada["modelId"] == "modelo-texto"
    assert "Dos semanas" in entrada and "Sí, en las hojas" in entrada and "¿Hay manchas?" in entrada
    assert "Nunca recomiendes tratamientos" in llamada["system"][0]["text"]


def test_conducir_al_inicio_pide_la_primera_pregunta() -> None:
    runtime = RuntimeFalso('{"pregunta": "¿Qué ves?"}')
    _ia(runtime).conducir(ContextoConsulta("GULUPA", "Lote 1", "Todo", (), None), None)
    assert "primera pregunta" in runtime.converse_llamadas[0]["messages"][0]["content"][0]["text"]


def test_conducir_con_la_consulta_completa_y_fuera_de_dominio() -> None:
    turno = _ia(RuntimeFalso('{"pregunta": null, "completo": true, "fuera_de_dominio": true}')).conducir(CONTEXTO, "x")
    assert turno.pregunta is None and turno.completo and turno.fuera_de_dominio


def test_si_bedrock_falla_se_lanza_error_ia_sin_filtrar_detalles() -> None:
    with pytest.raises(ErrorIA, match="ValueError"):
        _ia(RuntimeFalso(error=ValueError("detalle interno"))).conducir(CONTEXTO, "x")


def test_si_la_ia_no_responde_json_se_lanza_error_ia() -> None:
    with pytest.raises(ErrorIA):
        _ia(RuntimeFalso("lo siento, no puedo")).conducir(CONTEXTO, "x")


def test_analizar_manda_las_fotos_como_bytes_y_usa_el_modelo_de_vision() -> None:
    respuesta = {
        "categorias": {
            "RONA": {"porcentaje": 72.5, "coinciden": ["manchas"], "descartan": []},
            "TRIPS": {"porcentaje": 20, "coinciden": [], "descartan": ["no hay plaga"]},
        },
        "explicacion": "Coinciden las manchas.",
    }
    runtime = RuntimeFalso(json.dumps(respuesta))
    jpeg, png = b"\xff\xd8\xff\xe0datos", b"\x89PNG\r\n\x1a\ndatos"
    analisis = _ia(runtime).analizar(CONTEXTO, [jpeg, png], ["fragmento técnico"], CATEGORIAS)
    assert analisis.porcentajes == {"RONA": 72.5, "TRIPS": 20.0}
    assert analisis.detalle["RONA"]["coinciden"] == ["manchas"] and analisis.explicacion == "Coinciden las manchas."
    llamada = runtime.converse_llamadas[0]
    assert llamada["modelId"] == "modelo-vision"
    contenido = llamada["messages"][0]["content"]
    assert contenido[1] == {"image": {"format": "jpeg", "source": {"bytes": jpeg}}}
    assert contenido[2]["image"]["format"] == "png"
    assert "fragmento técnico" in contenido[0]["text"] and "BOTRYTIS, RONA, TRIPS" in contenido[0]["text"]


def test_analizar_ignora_codigos_inventados_y_acota_los_porcentajes() -> None:
    respuesta = {
        "categorias": {
            "INVENTADA": {"porcentaje": 99},
            "RONA": {"porcentaje": 140},
            "TRIPS": {"porcentaje": -5},
            "BOTRYTIS": {"porcentaje": "mucho"},
            "FUSARIUM": "texto suelto",
        }
    }
    analisis = _ia(RuntimeFalso(json.dumps(respuesta))).analizar(CONTEXTO, [], [], [*CATEGORIAS, "FUSARIUM"])
    assert analisis.porcentajes == {"RONA": 100.0, "TRIPS": 0.0}


def test_analizar_sin_categorias_es_un_error() -> None:
    with pytest.raises(ErrorIA, match="categorías"):
        _ia(RuntimeFalso('{"explicacion": "nada"}')).analizar(CONTEXTO, [], [], CATEGORIAS)


def test_embedding_devuelve_el_vector_y_pide_la_dimension_configurada() -> None:
    runtime = RuntimeFalso(vector=[0.1, 0.2, 0.3])
    assert _ia(runtime).embedding("manchas en las hojas") == [0.1, 0.2, 0.3]
    llamada = runtime.invoke_llamadas[0]
    assert llamada["modelId"] == "modelo-embed"
    assert json.loads(llamada["body"])["dimensions"] == 3


def test_embedding_con_dimension_equivocada_es_un_error() -> None:
    with pytest.raises(ErrorIA, match="dimensiones"):
        _ia(RuntimeFalso(vector=[0.1, 0.2])).embedding("x")


def test_embedding_si_bedrock_falla_es_error_ia() -> None:
    with pytest.raises(ErrorIA):
        _ia(RuntimeFalso(error=TimeoutError())).embedding("x")


def test_sin_modelos_configurados_no_arranca(monkeypatch: pytest.MonkeyPatch) -> None:
    sin_modelos = get_settings().model_copy(
        update={"bedrock_model_text": None, "bedrock_model_vision": None, "bedrock_model_embed": None}
    )
    monkeypatch.setattr(bedrock_client, "get_settings", lambda: sin_modelos)
    with pytest.raises(RuntimeError, match="BEDROCK_MODEL"):
        bedrock_client.get_bedrock_ai()
