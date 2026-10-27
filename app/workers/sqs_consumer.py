"""Consumidor de la cola de mensajes entrantes: `python -m app.workers.sqs_consumer` (guía §11, módulo 9).

Corre como un servicio ECS propio (no dentro de la API) para que, si la API escala, el consumo no se duplique.

- **Long polling**: espera mensajes en la cola sin gastar llamadas.
- **Borrado tras procesar**: un mensaje solo se borra de la cola cuando ya se procesó; si el proceso se cae a la mitad,
  vuelve a aparecer.
- **Idempotencia** por `wamid` (ver `modules/consumer/procesador.py`): un mensaje repetido no se procesa dos veces.
- **Orden por conversación**: la cola es FIFO con el celular como grupo. Si un mensaje falla, los siguientes de ese
  mismo celular que ya venían en el lote se liberan sin procesar, para no saltarse el orden.
- **Reintentos con backoff**: un fallo aplaza el reintento (5, 10, 20, 40, 60 s…). Tras las recepciones definidas en la
  cola (5), SQS lo manda solo a la **DLQ**.
- **Apagado ordenado**: con SIGTERM/SIGINT termina el mensaje en curso y libera el resto del lote.

Código de salida: 0 apagado normal · 1 error inesperado · 2 configuración inválida (p. ej. el visibility timeout de la
cola no supera el tiempo máximo de procesamiento).
"""

import logging
import signal
import sys
import threading
import time
from dataclasses import dataclass
from types import FrameType
from typing import Any

from sqlalchemy import create_engine

from app.core.config import Settings, get_database_settings, get_settings
from app.core.logging import configure_logging
from app.infrastructure.ai_client import get_ai_client
from app.infrastructure.conversaciones_store import get_conversaciones_store
from app.infrastructure.s3_storage_client import S3StorageClient, build_boto3_s3_client
from app.infrastructure.sqs_queue_client import MensajeSqs, SqsQueueClient, build_boto3_sqs_client
from app.infrastructure.whatsapp_client import get_whatsapp_client
from app.modules.consumer.procesador import MensajeInvalido, Procesador, ResultadoProceso
from app.modules.orchestrator.orquestador import Orquestador
from app.modules.orchestrator.puertos import IdentificadorSimulado, PipelineSimulado

logger = logging.getLogger("app.workers.sqs_consumer")

OK, ERROR, CONFIGURACION = 0, 1, 2
PAUSA_ANTE_ERROR_DE_COLA = 5.0


class ConfiguracionInvalida(RuntimeError):
    pass


def espera_reintento(recepciones: int, base: int, maximo: int) -> int:
    """Segundos hasta el próximo intento: duplica en cada recepción (base, 2×base, 4×base…) hasta el máximo."""
    return int(min(base * 2 ** (max(recepciones, 1) - 1), maximo))


@dataclass
class Resumen:
    recibidos: int = 0
    procesados: int = 0
    duplicados: int = 0
    fallidos: int = 0
    liberados: int = 0

    def sumar(self, otro: "Resumen") -> None:
        for campo in ("recibidos", "procesados", "duplicados", "fallidos", "liberados"):
            setattr(self, campo, getattr(self, campo) + getattr(otro, campo))


class Consumidor:
    def __init__(self, cola: SqsQueueClient, procesador: Procesador, ajustes: Settings | None = None) -> None:
        self.cola = cola
        self.procesador = procesador
        self.ajustes = ajustes or get_settings()
        self.detener = threading.Event()

    def pedir_detencion(self, *_: Any) -> None:
        """Apagado ordenado: se atiende el mensaje en curso y se sale."""
        self.detener.set()

    def validar_cola(self) -> None:
        """El mensaje debe seguir oculto mientras se procesa: el visibility timeout tiene que superar el máximo."""
        visibilidad = self.cola.visibility_timeout()
        maximo = self.ajustes.sqs_procesamiento_maximo_segundos
        if visibilidad <= maximo:
            raise ConfiguracionInvalida(
                f"El visibility timeout de la cola ({visibilidad} s) debe ser mayor que el procesamiento máximo "
                f"({maximo} s); si no, otro consumidor recibiría el mensaje mientras se procesa."
            )

    def _liberar(self, mensaje: MensajeSqs) -> None:
        try:
            self.cola.change_visibility(mensaje.receipt_handle, 0)
        except Exception:
            logger.exception("No se pudo liberar el mensaje", extra={"sqs_message_id": mensaje.message_id})

    def _aplazar(self, mensaje: MensajeSqs) -> None:
        espera = espera_reintento(
            mensaje.recepciones, self.ajustes.sqs_backoff_base_segundos, self.ajustes.sqs_backoff_maximo_segundos
        )
        try:
            self.cola.change_visibility(mensaje.receipt_handle, espera)
        except Exception:
            logger.exception("No se pudo aplazar el mensaje", extra={"sqs_message_id": mensaje.message_id})

    def procesar_lote(self, mensajes: list[MensajeSqs]) -> Resumen:
        resumen = Resumen(recibidos=len(mensajes))
        grupos_con_fallo: set[str] = set()
        for mensaje in mensajes:
            if self.detener.is_set() or mensaje.group_id in grupos_con_fallo:
                self._liberar(mensaje)  # sin procesar: se respeta el orden y, si es apagado, lo toma otro consumidor
                resumen.liberados += 1
                continue
            inicio = time.monotonic()
            try:
                resultado = self.procesador.procesar(mensaje.cuerpo)
            except Exception as error:
                resumen.fallidos += 1
                grupos_con_fallo.add(mensaje.group_id)
                (logger.error if isinstance(error, MensajeInvalido) else logger.exception)(
                    "No se pudo procesar el mensaje",
                    extra={
                        "sqs_message_id": mensaje.message_id,
                        "recepciones": mensaje.recepciones,
                        "motivo": str(error),
                    },
                )
                self._aplazar(mensaje)
                continue
            duracion = time.monotonic() - inicio
            if duracion > self.ajustes.sqs_procesamiento_maximo_segundos:
                logger.error("El mensaje tardó más del máximo permitido", extra={"segundos": round(duracion, 1)})
            if resultado is ResultadoProceso.DUPLICADO:
                resumen.duplicados += 1
            else:
                resumen.procesados += 1
            try:
                self.cola.delete(mensaje.receipt_handle)  # solo ahora, ya procesado
            except Exception:  # si no se borra, vuelve a llegar y se descarta como duplicado
                logger.exception("No se pudo borrar el mensaje", extra={"sqs_message_id": mensaje.message_id})
        return resumen

    def una_pasada(self) -> Resumen:
        mensajes = self.cola.receive(10, self.ajustes.sqs_espera_larga_segundos)
        return self.procesar_lote(mensajes)

    def ejecutar(self) -> Resumen:
        self.validar_cola()
        total = Resumen()
        logger.info("Consumidor iniciado")
        while not self.detener.is_set():
            try:
                total.sumar(self.una_pasada())
            except Exception:
                logger.exception("Falló la lectura de la cola; se reintenta")
                self.detener.wait(PAUSA_ANTE_ERROR_DE_COLA)
        logger.info("Consumidor detenido", extra={"resumen": total.__dict__})
        return total


def main() -> int:
    configure_logging("INFO")
    settings = get_settings()
    cola = SqsQueueClient(build_boto3_sqs_client(), settings.sqs_queue_url)
    try:
        orquestador = Orquestador(
            get_conversaciones_store(),
            get_whatsapp_client(),
            get_ai_client(),
            PipelineSimulado(),  # el pipeline y el identificador reales llegan en el módulo 11
            IdentificadorSimulado(),
            S3StorageClient(build_boto3_s3_client(), settings.s3_bucket_evidencias)
            if settings.s3_bucket_evidencias
            else None,
        )
    except RuntimeError as error:  # p. ej. WhatsApp o IA reales aún no disponibles, o falta la tabla de conversaciones
        print(str(error), file=sys.stderr)
        return CONFIGURACION
    engine = create_engine(get_database_settings().sqlalchemy_url)
    consumidor = Consumidor(cola, Procesador(engine, orquestador.manejar, orquestador.aplicar), settings)

    def apagar(numero: int, marco: FrameType | None) -> None:
        consumidor.pedir_detencion()

    signal.signal(signal.SIGTERM, apagar)
    signal.signal(signal.SIGINT, apagar)
    try:
        consumidor.ejecutar()
    except ConfiguracionInvalida as error:
        print(str(error), file=sys.stderr)
        return CONFIGURACION
    except Exception:
        logger.exception("El consumidor terminó con un error")
        return ERROR
    finally:
        engine.dispose()
    return OK


if __name__ == "__main__":
    raise SystemExit(main())
