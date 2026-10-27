"""Procesamiento idempotente de un mensaje entrante de WhatsApp (guía §11, módulo 9).

Garantía: **un mensaje (un `wamid`) se procesa una sola vez**, aunque llegue repetido (Meta reintenta; SQS entrega "al
menos una vez"). Se logra con una sola transacción que hace tres cosas a la vez:

1. inserta el mensaje en `mensajes_whatsapp` (el `wamid` es único: si ya estaba, es un duplicado y se descarta);
2. ejecuta el manejador (el orquestador conversacional, módulo 10);
3. confirma.

Si el manejador falla, la transacción se deshace y el mensaje deja de constar como recibido: el reintento lo procesa
completo. Si dos consumidores reciben el mismo `wamid` a la vez, el segundo espera a que termine el primero y entonces
lo ve como duplicado. Lo que el manejador haga fuera de la base (por ejemplo, enviar una respuesta) debe hacerlo
después de confirmar o ser tolerante a repeticiones.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core.logging import mask_phone

logger = logging.getLogger(__name__)


class MensajeInvalido(ValueError):
    """El cuerpo no es un mensaje que el webhook pudo haber encolado. Reintentar no lo arregla: termina en la DLQ."""


class ResultadoProceso(StrEnum):
    PROCESADO = "PROCESADO"
    DUPLICADO = "DUPLICADO"


@dataclass(frozen=True)
class MensajeEntrante:
    wamid: str
    telefono: str  # E.164 sin «+», tal como lo envía Meta (573001234567)
    tipo: str
    timestamp: str | None
    contenido: dict[str, Any]
    id_productor: str | None  # nulo si el número aún no está registrado (P-B08: lo registra el orquestador)


Manejador = Callable[[Session, MensajeEntrante], None]


def manejador_pendiente(sesion: Session, mensaje: MensajeEntrante) -> None:
    """Manejador provisional: el orquestador conversacional llega en el módulo 10. Solo deja constancia."""
    logger.info(
        "Mensaje recibido; el orquestador aún no está conectado",
        extra={"wamid": mensaje.wamid, "celular": mask_phone(mensaje.telefono), "tipo": mensaje.tipo},
    )


def interpretar(cuerpo: str) -> tuple[str, str, str, str | None, dict[str, Any]]:
    """(wamid, teléfono, tipo, timestamp, contenido) del JSON que encola el webhook, o `MensajeInvalido`."""
    try:
        datos = json.loads(cuerpo)
    except (TypeError, ValueError):
        raise MensajeInvalido("El cuerpo no es JSON") from None
    if not isinstance(datos, dict):
        raise MensajeInvalido("El cuerpo no es un objeto JSON")
    wamid, telefono, tipo = datos.get("wamid"), datos.get("from"), datos.get("type")
    contenido = datos.get("content", {})
    if not (isinstance(wamid, str) and wamid and isinstance(telefono, str) and telefono and isinstance(tipo, str)):
        raise MensajeInvalido("Faltan wamid, from o type")
    if not isinstance(contenido, dict):
        raise MensajeInvalido("El contenido debe ser un objeto")
    marca = datos.get("timestamp")
    return wamid, telefono, tipo, str(marca) if marca is not None else None, contenido


_REGISTRAR = text(
    """
    INSERT INTO mensajes_whatsapp
        (id_mensaje, wamid, telefono, tipo, contenido, estado_procesamiento, fecha_recepcion, id_productor)
    VALUES (fn_generar_id('seq_mensajes_whatsapp', 'MSG', '-', 12), CAST(:wamid AS varchar),
            CAST(:telefono AS varchar), CAST(:tipo AS varchar), CAST(:contenido AS jsonb), 'PROCESADO', fn_ahora(),
            (SELECT id_productor FROM productores WHERE telefono_productor = CAST(:telefono AS varchar)))
    ON CONFLICT (wamid) DO NOTHING
    RETURNING id_productor
    """
)


class Procesador:
    def __init__(self, engine: Engine, manejador: Manejador = manejador_pendiente) -> None:
        self.engine = engine
        self.manejador = manejador

    def procesar(self, cuerpo: str) -> ResultadoProceso:
        wamid, telefono, tipo, marca, contenido = interpretar(cuerpo)
        with Session(self.engine) as sesion, sesion.begin():
            fila = sesion.execute(
                _REGISTRAR,
                {
                    "wamid": wamid,
                    "telefono": telefono,
                    "tipo": tipo,
                    "contenido": json.dumps(contenido, ensure_ascii=False),
                },
            ).first()
            if fila is None:  # ya estaba: otro intento o un reintento de Meta lo había procesado
                logger.info("Mensaje duplicado descartado", extra={"wamid": wamid, "celular": mask_phone(telefono)})
                return ResultadoProceso.DUPLICADO
            self.manejador(sesion, MensajeEntrante(wamid, telefono, tipo, marca, contenido, fila[0]))
        return ResultadoProceso.PROCESADO
