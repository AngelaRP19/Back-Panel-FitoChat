"""Consumidor de la cola: long polling, borrado tras procesar, orden, reintentos, DLQ y apagado ordenado."""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core import config
from app.infrastructure.sqs_queue_client import SqsQueueClient
from app.modules.consumer.procesador import MensajeEntrante, Procesador
from app.workers import sqs_consumer
from app.workers.sqs_consumer import CONFIGURACION, ERROR, OK, ConfiguracionInvalida, Consumidor, espera_reintento
from tests.workers.conftest import MAX_RECEPCIONES, Colas, filas


def ajustes(**cambios: Any) -> Any:
    base = {"sqs_espera_larga_segundos": 0, "sqs_backoff_base_segundos": 0, "sqs_backoff_maximo_segundos": 0}
    return config.get_settings().model_copy(update={**base, **cambios})


class Manejador:
    """Manejador de prueba: anota los wamid y falla los que se le indiquen (tantas veces como se diga)."""

    def __init__(self, fallos: dict[str, int] | None = None) -> None:
        self.vistos: list[str] = []
        self.fallos = dict(fallos or {})
        self.al_ver: Any = None

    def __call__(self, sesion: Session, mensaje: MensajeEntrante) -> None:
        self.vistos.append(mensaje.wamid)
        if self.al_ver:
            self.al_ver(mensaje)
        if self.fallos.get(mensaje.wamid, 0) > 0:
            self.fallos[mensaje.wamid] -= 1
            raise RuntimeError(f"falla {mensaje.wamid}")


def consumidor(bd: Engine, colas: Colas, manejador: Manejador, **cambios: Any) -> Consumidor:
    return Consumidor(colas.cliente, Procesador(bd, manejador), ajustes(**cambios))


# --- backoff y configuración -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("recepciones", "espera"), [(0, 5), (1, 5), (2, 10), (3, 20), (4, 40), (5, 60), (6, 60), (20, 60)]
)
def test_el_backoff_se_duplica_hasta_el_maximo(recepciones: int, espera: int) -> None:
    assert espera_reintento(recepciones, 5, 60) == espera


def test_el_visibility_timeout_debe_superar_el_procesamiento_maximo(bd: Engine, colas: Colas) -> None:
    consumidor(bd, colas, Manejador()).validar_cola()  # 120 s > 90 s
    colas.crudo.set_queue_attributes(QueueUrl=colas.url, Attributes={"VisibilityTimeout": "30"})
    with pytest.raises(ConfiguracionInvalida, match="30 s"):
        consumidor(bd, colas, Manejador()).validar_cola()
    colas.crudo.set_queue_attributes(QueueUrl=colas.url, Attributes={"VisibilityTimeout": "90"})
    with pytest.raises(ConfiguracionInvalida):  # igual al máximo tampoco alcanza
        consumidor(bd, colas, Manejador()).validar_cola()


# --- procesar y borrar -------------------------------------------------------------------------------------------


def test_procesa_y_borra_de_la_cola(bd: Engine, colas: Colas) -> None:
    manejador = Manejador()
    colas.encolar("wamid.1", telefono="573001112233")
    colas.encolar("wamid.2", telefono="573009998877")
    resumen = consumidor(bd, colas, manejador).una_pasada()
    assert (resumen.recibidos, resumen.procesados, resumen.fallidos) == (2, 2, 0)
    assert sorted(manejador.vistos) == ["wamid.1", "wamid.2"] and colas.conteo() == (0, 0)  # nada visible ni en vuelo


def test_una_cola_vacia_no_hace_nada(bd: Engine, colas: Colas) -> None:
    resumen = consumidor(bd, colas, Manejador()).una_pasada()
    assert resumen.recibidos == 0


def test_un_duplicado_no_genera_dos_respuestas(bd: Engine, colas: Colas) -> None:
    """Meta reintenta y el mismo wamid llega dos veces a la cola (fuera de la ventana de deduplicación de SQS)."""
    manejador = Manejador()
    colas.encolar("wamid.1", dedup="intento-1")
    colas.encolar("wamid.1", dedup="intento-2")
    resumen = consumidor(bd, colas, manejador).una_pasada()
    assert (resumen.procesados, resumen.duplicados) == (1, 1)
    assert manejador.vistos == ["wamid.1"] and colas.conteo() == (0, 0)  # ambos copias salieron de la cola


def test_el_mensaje_solo_se_borra_despues_de_procesarlo(bd: Engine, colas: Colas) -> None:
    visto_en_la_cola: list[tuple[int, int]] = []
    manejador = Manejador()
    manejador.al_ver = lambda m: visto_en_la_cola.append(colas.conteo())
    colas.encolar("wamid.1")
    consumidor(bd, colas, manejador).una_pasada()
    assert visto_en_la_cola == [(0, 1)]  # mientras se procesa sigue en la cola, oculto
    assert colas.conteo() == (0, 0)  # y recién al terminar se borra


def test_si_no_se_puede_borrar_igual_cuenta_y_el_reintento_lo_descarta_como_duplicado(bd: Engine, colas: Colas) -> None:
    class FallaAlBorrar(SqsQueueClient):
        def delete(self, receipt_handle: str) -> None:
            self.change_visibility(receipt_handle, 0)  # equivale a que venza el visibility timeout
            raise ConnectionError("sin red")

    manejador = Manejador()
    cons = Consumidor(FallaAlBorrar(colas.crudo, colas.url), Procesador(bd, manejador), ajustes())
    colas.encolar("wamid.1")
    assert cons.una_pasada().procesados == 1  # se procesó aunque no se pudo borrar de la cola
    segunda = cons.una_pasada()  # el mensaje vuelve a llegar...
    assert (segunda.duplicados, segunda.procesados) == (1, 0)  # ...y se descarta sin repetir el trabajo
    assert manejador.vistos == ["wamid.1"]


# --- fallos, reintentos y DLQ ------------------------------------------------------------------------------------


def test_un_fallo_no_borra_el_mensaje_y_lo_aplaza(bd: Engine, colas: Colas) -> None:
    colas.encolar("wamid.1")
    resumen = consumidor(
        bd, colas, Manejador({"wamid.1": 1}), sqs_backoff_base_segundos=30, sqs_backoff_maximo_segundos=60
    ).una_pasada()
    assert (resumen.fallidos, resumen.procesados) == (1, 0)
    assert colas.conteo() == (0, 1)  # sigue en la cola, oculto durante el backoff
    assert filas(bd, "SELECT 1 FROM mensajes_whatsapp") == []


def test_el_reintento_lo_procesa_cuando_el_problema_se_resuelve(bd: Engine, colas: Colas) -> None:
    manejador = Manejador({"wamid.1": 1})
    cons = consumidor(bd, colas, manejador)  # backoff 0: vuelve a estar visible de inmediato
    colas.encolar("wamid.1")
    assert cons.una_pasada().fallidos == 1
    assert cons.una_pasada().procesados == 1
    assert manejador.vistos == ["wamid.1", "wamid.1"] and colas.conteo() == (0, 0)
    assert len(filas(bd, "SELECT 1 FROM mensajes_whatsapp")) == 1


def test_tras_las_recepciones_definidas_el_mensaje_pasa_a_la_dlq(bd: Engine, colas: Colas) -> None:
    colas.encolar("wamid.malo", texto="siempre falla")
    cons = consumidor(bd, colas, Manejador({"wamid.malo": 99}))
    for _ in range(MAX_RECEPCIONES):
        assert cons.una_pasada().fallidos == 1
    assert cons.una_pasada().recibidos == 0  # a la siguiente recepción SQS lo mueve a la DLQ
    assert colas.conteo() == (0, 0)
    [en_dlq] = colas.en_dlq()
    assert en_dlq["wamid"] == "wamid.malo" and en_dlq["content"] == {"text": "siempre falla"}
    assert filas(bd, "SELECT 1 FROM mensajes_whatsapp") == []


def test_un_mensaje_malformado_termina_en_la_dlq_y_no_traba_la_cola(bd: Engine, colas: Colas) -> None:
    colas.crudo.send_message(
        QueueUrl=colas.url, MessageBody="esto no es JSON", MessageGroupId="573000000000", MessageDeduplicationId="malo"
    )
    colas.encolar("wamid.bueno", telefono="573001112233")
    manejador = Manejador()
    cons = consumidor(bd, colas, manejador)
    resumen = cons.una_pasada()
    assert (resumen.fallidos, resumen.procesados) == (1, 1)  # el malo no frena a otro grupo
    for _ in range(MAX_RECEPCIONES - 1):
        cons.una_pasada()
    cons.una_pasada()
    assert manejador.vistos == ["wamid.bueno"] and colas.conteo() == (0, 0)
    assert len(colas.crudo.receive_message(QueueUrl=colas.url_dlq).get("Messages", [])) == 1


def test_una_falla_de_la_cola_al_leer_no_detiene_el_consumidor(
    bd: Engine, colas: Colas, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sqs_consumer, "PAUSA_ANTE_ERROR_DE_COLA", 0)
    manejador = Manejador()
    cons = consumidor(bd, colas, manejador)
    original = colas.cliente.receive
    llamadas = {"n": 0}

    def intermitente(maximo: int, espera: int) -> Any:
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            raise ConnectionError("SQS no responde")
        return original(maximo, espera)

    monkeypatch.setattr(colas.cliente, "receive", intermitente)
    colas.encolar("wamid.1")
    manejador.al_ver = lambda m: cons.pedir_detencion()
    resumen = cons.ejecutar()
    assert resumen.procesados == 1 and llamadas["n"] == 2


# --- orden por conversación --------------------------------------------------------------------------------------


def test_si_un_mensaje_falla_los_siguientes_de_la_misma_conversacion_esperan(bd: Engine, colas: Colas) -> None:
    manejador = Manejador({"A1": 1})
    cons = consumidor(bd, colas, manejador)
    colas.encolar("A1", telefono="573001112233")
    colas.encolar("A2", telefono="573001112233")  # misma conversación: no puede adelantarse a A1
    colas.encolar("B1", telefono="573009998877")  # otra conversación: sigue su curso
    primera = cons.una_pasada()
    assert (primera.fallidos, primera.procesados, primera.liberados) == (1, 1, 1)
    assert manejador.vistos == ["A1", "B1"]  # A2 no llegó al manejador
    # Garantía de la cola FIFO real: A2 no se entrega antes de que A1 se resuelva. moto no la reproduce con fidelidad
    # al reentregar, así que aquí solo se comprueba lo que depende de este consumidor: A2 no se procesó junto al fallo
    # de A1 y, con el problema resuelto, ambos acaban procesados y la cola queda vacía.
    cons.una_pasada()
    cons.una_pasada()
    assert sorted(manejador.vistos[2:]) == ["A1", "A2"] and colas.conteo() == (0, 0)


def test_los_mensajes_de_una_conversacion_se_procesan_en_orden(bd: Engine, colas: Colas) -> None:
    manejador = Manejador()
    for n in range(1, 6):
        colas.encolar(f"W{n}", telefono="573001112233")
    consumidor(bd, colas, manejador).una_pasada()
    assert manejador.vistos == ["W1", "W2", "W3", "W4", "W5"]


# --- apagado ordenado --------------------------------------------------------------------------------------------


def test_al_pedir_el_apagado_no_se_toma_trabajo_nuevo(bd: Engine, colas: Colas) -> None:
    manejador = Manejador()
    cons = consumidor(bd, colas, manejador)
    colas.encolar("wamid.1")
    cons.pedir_detencion()
    assert cons.ejecutar().recibidos == 0
    assert manejador.vistos == [] and colas.conteo() == (1, 0)  # el mensaje sigue disponible para otro consumidor


def test_el_apagado_termina_el_mensaje_en_curso_y_libera_el_resto_del_lote(bd: Engine, colas: Colas) -> None:
    manejador = Manejador()
    cons = consumidor(bd, colas, manejador)
    manejador.al_ver = lambda m: cons.pedir_detencion()  # llega la señal mientras se procesa el primero
    colas.encolar("W1", telefono="573001112233")
    colas.encolar("W2", telefono="573001112233")
    colas.encolar("W3", telefono="573009998877")
    resumen = cons.ejecutar()
    assert (resumen.procesados, resumen.liberados) == (1, 2)
    assert manejador.vistos == ["W1"] and colas.conteo() == (2, 0)  # los otros dos quedan listos para otro consumidor
    assert len(filas(bd, "SELECT 1 FROM mensajes_whatsapp")) == 1


# --- punto de entrada --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("efecto", "codigo"),
    [(None, OK), (ConfiguracionInvalida("visibility corto"), CONFIGURACION), (RuntimeError("inesperado"), ERROR)],
)
def test_codigos_de_salida(
    efecto: Exception | None, codigo: int, monkeypatch: pytest.MonkeyPatch, colas: Colas
) -> None:
    def ejecutar(self: Consumidor) -> None:
        if efecto:
            raise efecto

    monkeypatch.setattr(sqs_consumer, "build_boto3_sqs_client", lambda: colas.crudo)
    monkeypatch.setattr(Consumidor, "ejecutar", ejecutar)
    assert sqs_consumer.main() == codigo


# --- de punta a punta: webhook → cola → consumidor -----------------------------------------------------------------


def test_del_webhook_al_consumidor(bd: Engine, sqs: tuple[Any, str], client: TestClient) -> None:
    from app.modules.webhook.signature import compute_signature
    from tests.conftest import APP_SECRET
    from tests.payloads import PHONE, text_message

    payload = json.dumps(text_message("wamid.E2E", PHONE)).encode()
    respuesta = client.post(
        "/webhook/whatsapp",
        content=payload,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": compute_signature(payload, APP_SECRET)},
    )
    assert respuesta.status_code == 200
    sqs_client, url = sqs
    manejador = Manejador()
    cons = Consumidor(SqsQueueClient(sqs_client, url), Procesador(bd, manejador), ajustes())
    assert cons.una_pasada().procesados == 1
    assert manejador.vistos == ["wamid.E2E"]
    [fila] = filas(bd, "SELECT telefono, tipo FROM mensajes_whatsapp")
    assert (fila["telefono"], fila["tipo"]) == (PHONE, "text")


# --- Armado del orquestador según la integración de IA (módulo 11) ----------------------------------------------


def _armar(monkeypatch: pytest.MonkeyPatch, **cambios: Any) -> Any:
    from app.infrastructure.ai_client import AiSimulado
    from app.infrastructure.conversaciones_store import ConversacionesEnMemoria
    from app.infrastructure.rekognition_client import ModeradorSimulado
    from app.infrastructure.whatsapp_client import WhatsAppSimulado

    monkeypatch.setattr(sqs_consumer, "get_ai_client", lambda: AiSimulado())
    monkeypatch.setattr(sqs_consumer, "get_whatsapp_client", lambda: WhatsAppSimulado())
    monkeypatch.setattr(sqs_consumer, "get_conversaciones_store", lambda: ConversacionesEnMemoria())
    monkeypatch.setattr(sqs_consumer, "get_moderador", lambda: ModeradorSimulado())
    monkeypatch.setattr(sqs_consumer, "build_boto3_s3_client", lambda: object())
    return sqs_consumer.construir_orquestador(config.get_settings().model_copy(update=cambios))


def test_con_ia_simulada_el_pipeline_y_el_identificador_son_simulados(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.orchestrator.puertos import IdentificadorSimulado, PipelineSimulado

    orq = _armar(monkeypatch, integracion_ia="simulada")
    assert isinstance(orq.pipeline, PipelineSimulado) and isinstance(orq.identificador, IdentificadorSimulado)


def test_con_ia_real_se_usan_el_pipeline_y_el_identificador_reales(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.identification.servicio import IdentificadorIA
    from app.modules.image_pipeline.pipeline import PipelineReal

    orq = _armar(monkeypatch, integracion_ia="real", s3_bucket_evidencias="bucket-de-prueba")
    assert isinstance(orq.pipeline, PipelineReal) and isinstance(orq.identificador, IdentificadorIA)


def test_con_ia_real_sin_bucket_no_arranca(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="S3_BUCKET_EVIDENCIAS"):
        _armar(monkeypatch, integracion_ia="real", s3_bucket_evidencias=None)
