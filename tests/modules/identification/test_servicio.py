"""Identificación preliminar completa: RAG en pgvector + IA + reglas + guardrail, dentro de una conversación real."""

from collections import deque
from typing import Any

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import text as sql

from app.core.config import get_settings
from app.infrastructure.ai_client import AiSimulado, AnalisisIA, ContextoConsulta, ErrorIA, Turno
from app.infrastructure.db.models.arquitectura import EMBEDDING_DIM
from app.modules.consumer.procesador import Procesador
from app.modules.identification.servicio import EXPLICACION_NEUTRA, IdentificadorIA, literal_vector
from app.modules.orchestrator import textos as t
from app.modules.orchestrator.orquestador import Orquestador
from tests.modules.orchestrator.conftest import Sistema, productor_con_finca, programar

FOTO = b"bytes-de-la-foto"


def vector(*posiciones: int) -> list[float]:
    """Vector unitario con 1 en las posiciones indicadas (y 0 en el resto)."""
    return [1.0 if i in posiciones else 0.0 for i in range(EMBEDDING_DIM)]


class IaEspia(AiSimulado):
    """Anota con qué se la consultó y puede fallar las primeras veces."""

    def __init__(self) -> None:
        super().__init__(dimension=EMBEDDING_DIM)
        self.consulta_embedding: list[str] = []
        self.recibido: dict[str, Any] = {}
        self.fallos_embedding = 0
        self.fallos_analisis = 0
        self.vector_consulta = vector(0)

    def embedding(self, texto: str) -> list[float]:
        self.consulta_embedding.append(texto)
        if self.fallos_embedding > 0:
            self.fallos_embedding -= 1
            raise ErrorIA("embedding caído")
        return self.vector_consulta

    def analizar(
        self, contexto: ContextoConsulta, imagenes: list[bytes], conocimiento: list[str], categorias: list[str]
    ) -> AnalisisIA:
        self.recibido = {
            "contexto": contexto,
            "imagenes": imagenes,
            "conocimiento": conocimiento,
            "categorias": categorias,
        }
        if self.fallos_analisis > 0:
            self.fallos_analisis -= 1
            raise ErrorIA("análisis caído")
        return super().analizar(contexto, imagenes, conocimiento, categorias)


class S3Falso:
    """Sirve `FOTO` para cualquier clave, o solo los objetos dados (con `{}`, S3 no tiene nada)."""

    def __init__(self, objetos: dict[str, bytes] | None = None) -> None:
        self.objetos = objetos

    def descargar(self, clave: str) -> bytes:
        if self.objetos is None:
            return FOTO
        if clave not in self.objetos:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "x"}}, "GetObject")
        return self.objetos[clave]


def preparar(sistema: Sistema, ai: IaEspia, objetos: dict[str, bytes] | None = None) -> IdentificadorIA:
    """Cambia el identificador programable del sistema por el real, con IA espía y S3 falso."""
    ajustes = get_settings().model_copy(update={"ia_reintentos": 2, "ia_pausa_reintento_segundos": 0.0})
    identificador = IdentificadorIA(ai, S3Falso(objetos), ajustes)  # type: ignore[arg-type]
    orq = Orquestador(
        sistema.store,
        sistema.whatsapp,
        ai,
        sistema.pipeline,
        identificador,
        sistema.s3,  # type: ignore[arg-type]
        ajustes,
        dormir=lambda _: None,
    )
    sistema.ai = ai  # para que `programar` le deje los turnos a la IA espía
    sistema.procesador = Procesador(sistema.motor, orq.manejar, orq.aplicar)
    return identificador


def conocimiento(sistema: Sistema) -> None:
    with sistema.motor.begin() as c:
        for n, (texto, v) in enumerate(
            [("Fragmento de roña", vector(0)), ("Fragmento de trips", vector(1)), ("Fragmento de botrytis", vector(2))],
            start=1,
        ):
            c.execute(
                sql(
                    "INSERT INTO conocimiento_embeddings (id_conocimiento, fuente, texto, embedding) "
                    "VALUES (:i, 'prueba', :t, CAST(:v AS vector))"
                ),
                {"i": f"CON-{n:08d}", "t": texto, "v": str(v)},
            )


def enviar_consulta(sistema: Sistema, *turnos: Turno) -> list[str]:
    """Conversación completa con una foto hasta enviar al panel; devuelve las últimas respuestas."""
    programar(sistema, *turnos)
    productor_con_finca(sistema.motor)
    sistema.escribir("hola")
    sistema.escribir("1")
    for respuesta in ("hace una semana", "en las hojas bajas"):
        if sistema.paso() == "CUESTIONARIO":
            sistema.escribir(respuesta)
    sistema.foto("m1", "una hoja")
    sistema.escribir("listo")
    return sistema.escribir("sí")


def resultado_guardado(sistema: Sistema) -> list[dict[str, Any]]:
    return sistema.consulta("SELECT * FROM identificacion_preliminar")


PREGUNTAS = (Turno(pregunta="¿Cuándo empezó?"), Turno(pregunta="¿Dónde se ve?"), Turno(completo=True))


def test_una_categoria_sobre_el_umbral_queda_como_preliminar(sistema: Sistema) -> None:
    ai = IaEspia()
    ai.analisis = AnalisisIA(
        {"RONA": 72.456, "TRIPS": 30.0},
        {"RONA": {"coinciden": ["manchas en el envés"], "descartan": []}},
        "Coinciden las manchas con la roña.",
    )
    preparar(sistema, ai)
    respuestas = enviar_consulta(sistema, *PREGUNTAS)
    assert t.RESULTADO_PRELIMINAR.format(resultado="Roña") in respuestas
    [ident] = resultado_guardado(sistema)
    assert (
        ident["tipo_resultado"] == "PRELIMINAR"
        and ident["explicacion_resultado"] == "Coinciden las manchas con la roña."
    )
    coincidencias = sistema.consulta(
        "SELECT c.nombre_categoria AS n, i.porcentaje_coincidencia AS p FROM coincidencias_categoria i "
        "JOIN categorias_fitosanitarias c USING (id_categoria) ORDER BY 2 DESC"
    )
    assert [(c["n"], float(c["p"])) for c in coincidencias] == [("Roña", 72.46), ("Trips", 30.0)]
    assert ident["detalle_explicacion"]["RONA"]["coinciden"] == ["manchas en el envés"]
    assert sistema.estado_caso() == "Pendiente de revisión"


def test_la_ia_recibe_sintomas_fotos_fragmentos_y_categorias(sistema: Sistema) -> None:
    ai = IaEspia()
    ai.vector_consulta = vector(1)  # se parece al fragmento de trips
    preparar(sistema, ai)
    conocimiento(sistema)
    enviar_consulta(sistema, *PREGUNTAS)
    recibido = ai.recibido
    assert recibido["imagenes"] == [FOTO]
    assert recibido["conocimiento"][0] == "Fragmento de trips"  # el más cercano primero (distancia coseno)
    assert len(recibido["conocimiento"]) == 3
    assert recibido["categorias"] == ["BOTRYTIS", "RONA", "DEFICIENCIA_MAGNESIO", "TRIPS", "FUSARIUM"]
    contexto = recibido["contexto"]
    assert contexto.especie == "GULUPA" and contexto.pregunta_pendiente is None
    assert [r for _, r in contexto.historial] == ["hace una semana", "en las hojas bajas"]
    assert "hace una semana" in ai.consulta_embedding[0] and "GULUPA" in ai.consulta_embedding[0]


def test_dos_categorias_cercanas_son_ambiguas_y_se_le_dice_al_productor(sistema: Sistema) -> None:
    ai = IaEspia()
    ai.analisis = AnalisisIA({"RONA": 70.0, "TRIPS": 60.0}, {}, "Se parecen.")
    preparar(sistema, ai)
    respuestas = enviar_consulta(sistema, *PREGUNTAS)
    assert t.RESULTADO_AMBIGUO.format(primera="Roña", segunda="Trips") in respuestas
    assert resultado_guardado(sistema)[0]["tipo_resultado"] == "AMBIGUO"


def test_si_ninguna_llega_al_umbral_no_se_identifica_nada(sistema: Sistema) -> None:
    ai = IaEspia()
    ai.analisis = AnalisisIA({"RONA": 49.0, "TRIPS": 10.0}, {}, "No hay coincidencias claras.")
    preparar(sistema, ai)
    respuestas = enviar_consulta(sistema, *PREGUNTAS)
    assert resultado_guardado(sistema)[0]["tipo_resultado"] == "NO_IDENTIFICADO"
    assert not any("resultado preliminar" in r or "más de una posibilidad" in r for r in respuestas)
    assert sistema.estado_caso() == "Pendiente de revisión"


def test_lo_que_la_ia_escribe_pasa_por_el_guardrail(sistema: Sistema) -> None:
    ai = IaEspia()
    ai.analisis = AnalisisIA(
        {"RONA": 80.0},
        {"RONA": {"coinciden": ["manchas", "Aplique 2 ml de fungicida"], "descartan": ["probabilidad baja de trips"]}},
        "Hay una probabilidad alta; aplique un fungicida.",
    )
    preparar(sistema, ai)
    enviar_consulta(sistema, *PREGUNTAS)
    [ident] = resultado_guardado(sistema)
    assert ident["explicacion_resultado"] == EXPLICACION_NEUTRA
    assert ident["detalle_explicacion"]["RONA"] == {"coinciden": ["manchas"], "descartan": []}


def test_codigos_desconocidos_de_la_ia_se_ignoran(sistema: Sistema) -> None:
    ai = IaEspia()
    ai.analisis = AnalisisIA({"MILDIU": 99.0, "RONA": 55.0}, {}, "")
    preparar(sistema, ai)
    enviar_consulta(sistema, *PREGUNTAS)
    [ident] = resultado_guardado(sistema)
    assert ident["tipo_resultado"] == "PRELIMINAR" and ident["explicacion_resultado"] == EXPLICACION_NEUTRA
    assert len(sistema.consulta("SELECT 1 FROM coincidencias_categoria")) == 1


def test_un_fallo_pasajero_de_la_ia_se_reintenta(sistema: Sistema) -> None:
    ai = IaEspia()
    ai.fallos_embedding, ai.fallos_analisis = 1, 1
    ai.analisis = AnalisisIA({"TRIPS": 90.0}, {}, "Coincide.")
    preparar(sistema, ai)
    enviar_consulta(sistema, *PREGUNTAS)
    assert resultado_guardado(sistema)[0]["tipo_resultado"] == "PRELIMINAR" and len(ai.consulta_embedding) == 2


@pytest.mark.parametrize("que_falla", ["fallos_embedding", "fallos_analisis"])
def test_si_la_ia_no_responde_el_caso_llega_al_panel_sin_identificacion(sistema: Sistema, que_falla: str) -> None:
    ai = IaEspia()
    setattr(ai, que_falla, 5)
    preparar(sistema, ai)
    respuestas = enviar_consulta(sistema, *PREGUNTAS)
    assert respuestas[0] == t.ENVIADO and len(respuestas) == 1  # sin resultado que contar
    assert resultado_guardado(sistema) == []
    [caso] = sistema.consulta("SELECT * FROM casos")
    assert caso["id_identificacion_preliminar"] is None and sistema.estado_caso() == "Pendiente de revisión"


def test_una_foto_que_no_se_puede_leer_de_s3_se_omite(sistema: Sistema) -> None:
    ai = IaEspia()
    ai.analisis = AnalisisIA({"RONA": 60.0}, {}, "Coincide.")
    preparar(sistema, ai, {})  # S3 no tiene la foto
    enviar_consulta(sistema, *PREGUNTAS)
    assert ai.recibido["imagenes"] == [] and resultado_guardado(sistema)[0]["tipo_resultado"] == "PRELIMINAR"


def test_sin_conocimiento_cargado_la_ia_analiza_igual(sistema: Sistema) -> None:
    ai = IaEspia()
    ai.analisis = AnalisisIA({"RONA": 60.0}, {}, "Coincide.")
    preparar(sistema, ai)
    enviar_consulta(sistema, *PREGUNTAS)
    assert ai.recibido["conocimiento"] == []


def test_el_literal_del_vector_es_el_de_pgvector() -> None:
    assert literal_vector([0.5, 0.0, 1.0]) == "[0.5,0,1]"


def test_ia_simulada_sin_turnos_deja_el_tipo_de_deque() -> None:
    assert isinstance(AiSimulado().turnos, deque)
