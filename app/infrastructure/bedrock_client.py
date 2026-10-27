"""Cliente real de IA sobre Amazon Bedrock (guía §11, módulo 11): Claude con visión y texto, y embeddings.

La IA devuelve JSON; aquí se arma el prompt, se llama a `converse` y se interpreta la respuesta. Nada de lo que la IA
escribe llega al productor sin pasar por el guardrail (lo aplican el orquestador y el identificador). La imagen viaja
como bytes en cada llamada: la cuenta de IA no puede leer el bucket de evidencias.
"""

import json
from collections.abc import Callable
from typing import Any

from app.core.config import get_database_settings, get_settings
from app.infrastructure.ai_client import AnalisisIA, ContextoConsulta, ErrorIA, Turno
from app.infrastructure.ia_credenciales import ProveedorClientesIA, get_proveedor_ia

SISTEMA_CUESTIONARIO = """Eres un asistente fitosanitario de la ANUC Zetaquirá que conversa por WhatsApp con \
campesinos \
colombianos sobre cultivos de gulupa, granadilla y maracuyá (Passiflora). Haces preguntas cortas, claras y sencillas, \
de una en una, para entender los síntomas de la planta afectada y decidir cuándo ya hay información suficiente.
REGLAS: (1) Nunca recomiendes tratamientos, productos, dosis ni frecuencias; si preguntan por eso, no lo hagas. \
(2) Si lo que dice el productor no tiene que ver con el cultivo ni con sus problemas fitosanitarios, márcalo como \
fuera de dominio. (3) Nunca uses las palabras «confianza» ni «probabilidad»: la identificación es preliminar. \
(4) Responde SOLO con un objeto JSON: {"pregunta": texto o null, "completo": true/false, "relevante": true/false, \
"fuera_de_dominio": true/false}. «relevante» indica si la última respuesta sirve para el diagnóstico."""

SISTEMA_ANALISIS = """Eres un asistente fitosanitario de la ANUC Zetaquirá. Con los síntomas descritos, las fotos y \
los \
fragmentos de conocimiento técnico, estima para CADA categoría el porcentaje de coincidencia de síntomas (0 a 100). \
La identificación es preliminar: la decisión es del técnico. REGLAS: (1) Nunca recomiendes tratamientos, productos, \
dosis ni frecuencias. (2) Nunca uses las palabras «confianza» ni «probabilidad». (3) Responde SOLO con un objeto JSON: \
{"categorias": {"<CÓDIGO>": {"porcentaje": número, "coinciden": [síntomas que coinciden], "descartan": [por qué se \
descarta]}}, "explicacion": texto corto en español}. Usa exactamente los códigos de categoría que se te dan."""


def extraer_json(texto: str) -> dict[str, Any]:
    """El primer objeto JSON de la respuesta (el modelo a veces lo rodea de texto o de ```)."""
    inicio, fin = texto.find("{"), texto.rfind("}")
    if inicio < 0 or fin <= inicio:
        raise ErrorIA("La respuesta de la IA no trae JSON")
    try:
        datos = json.loads(texto[inicio : fin + 1])
    except ValueError:
        raise ErrorIA("La respuesta de la IA no es un JSON válido") from None
    if not isinstance(datos, dict):
        raise ErrorIA("La respuesta de la IA no es un objeto")
    return datos


def _formato_imagen(datos: bytes) -> str:
    if datos[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if datos[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return "webp"
    return "jpeg"


class BedrockAi:
    def __init__(
        self,
        clientes: Callable[[], Any],
        modelo_texto: str,
        modelo_vision: str,
        modelo_embed: str,
        dimension: int,
    ) -> None:
        self._runtime = clientes  # devuelve el cliente vigente en cada llamada (las credenciales se renuevan)
        self._texto = modelo_texto
        self._vision = modelo_vision
        self._embed = modelo_embed
        self._dimension = dimension

    def _converse(self, modelo: str, sistema: str, contenido: list[dict[str, Any]], max_tokens: int) -> str:
        try:
            respuesta = self._runtime().converse(
                modelId=modelo,
                system=[{"text": sistema}],
                messages=[{"role": "user", "content": contenido}],
                inferenceConfig={"maxTokens": max_tokens, "temperature": 0.2},
            )
            bloques = respuesta["output"]["message"]["content"]
            return "".join(b.get("text", "") for b in bloques)
        except ErrorIA:
            raise
        except Exception as error:
            raise ErrorIA(f"Falló la llamada a Bedrock: {type(error).__name__}") from error

    def conducir(self, contexto: ContextoConsulta, mensaje: str | None) -> Turno:
        historial = (
            "\n".join(f"- Pregunta: {p}\n  Respuesta: {r}" for p, r in contexto.historial) or "(todavía ninguna)"
        )
        entrada = (
            f"Especie afectada: {contexto.especie}. Cultivo: {contexto.cultivo}. "
            f"Zona afectada: {contexto.zona_afectada}.\n"
            f"Preguntas y respuestas hasta ahora:\n{historial}\n"
        )
        if contexto.pregunta_pendiente is not None and mensaje is not None:
            entrada += f"Última pregunta: {contexto.pregunta_pendiente}\nRespuesta del productor: {mensaje}\n"
        else:
            entrada += "El productor aún no ha respondido nada: haz la primera pregunta.\n"
        datos = extraer_json(self._converse(self._texto, SISTEMA_CUESTIONARIO, [{"text": entrada}], 400))
        pregunta = datos.get("pregunta")
        return Turno(
            pregunta=str(pregunta).strip() if isinstance(pregunta, str) and pregunta.strip() else None,
            completo=bool(datos.get("completo", False)),
            relevante=bool(datos.get("relevante", True)),
            fuera_de_dominio=bool(datos.get("fuera_de_dominio", False)),
        )

    def analizar(
        self, contexto: ContextoConsulta, imagenes: list[bytes], conocimiento: list[str], categorias: list[str]
    ) -> AnalisisIA:
        historial = "\n".join(f"- {p}: {r}" for p, r in contexto.historial) or "(sin respuestas)"
        fragmentos = "\n".join(f"[{n}] {t}" for n, t in enumerate(conocimiento, start=1)) or "(sin fragmentos)"
        entrada = (
            f"Especie: {contexto.especie}. Zona afectada: {contexto.zona_afectada}.\nSíntomas descritos:\n{historial}\n"
            f"Conocimiento técnico de apoyo:\n{fragmentos}\nCategorías (usa estos códigos): {', '.join(categorias)}."
        )
        contenido: list[dict[str, Any]] = [{"text": entrada}]
        contenido += [{"image": {"format": _formato_imagen(i), "source": {"bytes": i}}} for i in imagenes]
        datos = extraer_json(self._converse(self._vision, SISTEMA_ANALISIS, contenido, 1500))
        crudas = datos.get("categorias")
        if not isinstance(crudas, dict):
            raise ErrorIA("La IA no devolvió categorías")
        porcentajes: dict[str, float] = {}
        detalle: dict[str, dict[str, list[str]]] = {}
        for codigo, valor in crudas.items():
            if codigo not in categorias or not isinstance(valor, dict):
                continue  # un código inventado se ignora
            try:
                porcentajes[codigo] = max(0.0, min(100.0, float(valor.get("porcentaje", 0))))
            except (TypeError, ValueError):
                continue
            detalle[codigo] = {
                "coinciden": [str(x) for x in valor.get("coinciden", []) if isinstance(x, str)],
                "descartan": [str(x) for x in valor.get("descartan", []) if isinstance(x, str)],
            }
        return AnalisisIA(porcentajes, detalle, str(datos.get("explicacion", "")).strip())

    def embedding(self, texto: str) -> list[float]:
        try:
            respuesta = self._runtime().invoke_model(
                modelId=self._embed,
                body=json.dumps({"inputText": texto[:8000], "dimensions": self._dimension, "normalize": True}),
                contentType="application/json",
                accept="application/json",
            )
            vector = json.loads(respuesta["body"].read())["embedding"]
        except Exception as error:
            raise ErrorIA(f"Falló el embedding: {type(error).__name__}") from error
        if len(vector) != self._dimension:
            raise ErrorIA(f"El embedding tiene {len(vector)} dimensiones y se esperaban {self._dimension}")
        return [float(x) for x in vector]


def get_bedrock_ai(proveedor: ProveedorClientesIA | None = None) -> BedrockAi:
    ajustes = get_settings()
    if not (ajustes.bedrock_model_text and ajustes.bedrock_model_vision and ajustes.bedrock_model_embed):
        raise RuntimeError("Faltan BEDROCK_MODEL_TEXT, BEDROCK_MODEL_VISION y BEDROCK_MODEL_EMBED")
    origen = proveedor or get_proveedor_ia()
    return BedrockAi(
        lambda: origen.cliente("bedrock-runtime"),
        ajustes.bedrock_model_text,
        ajustes.bedrock_model_vision,
        ajustes.bedrock_model_embed,
        get_database_settings().embedding_dim,
    )
