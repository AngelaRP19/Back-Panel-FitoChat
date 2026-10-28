"""Notificaciones de la campana del panel (D-20): DynamoDB `anuc-notificaciones`, sin preferencias (D-16).

Tabla con clave de partición `destinatario` (id de la cuenta: `LID-…` o `TEC-…`) y clave de orden `fecha_id`
(`<ISO 8601 UTC>#<NOT-id>`), así una sola Query trae lo de los últimos días en orden. `expira_en` es el atributo TTL:
DynamoDB borra solas las de más de 30 días, sin job. Los ids `NOT-` se generan aquí con un sufijo aleatorio (DynamoDB
no tiene secuencias). Detrás de una interfaz, con una versión en memoria para las pruebas y para correr en local.

Escribir una notificación nunca debe tumbar la operación que la origina: quien publica captura y registra el error.
"""

import logging
import secrets
import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Protocol

import boto3
from botocore.config import Config

from app.core.config import get_settings

logger = logging.getLogger(__name__)

VIGENCIA_DIAS = 30  # TTL: lo que pasa de aquí lo borra DynamoDB
DIAS_VISIBLES = 7  # la campana muestra los últimos 7 días


def nuevo_id() -> str:
    return f"NOT-{secrets.token_hex(5).upper()}"


@dataclass(frozen=True)
class Notificacion:
    destinatario: str
    id: str
    en: datetime  # UTC
    tipo: str
    titulo: str
    texto: str
    enlace: str  # ruta del panel
    leida: bool = False

    @property
    def fecha_id(self) -> str:
        return f"{self.en.astimezone(UTC).isoformat()}#{self.id}"


def desde_visibles(ahora: datetime | None = None) -> datetime:
    return (ahora or datetime.now(UTC)) - timedelta(days=DIAS_VISIBLES)


class NotificacionesStore(Protocol):
    def publicar(self, notificacion: Notificacion) -> None: ...

    def listar(self, destinatario: str, desde: datetime) -> list[Notificacion]:
        """De la más reciente a la más antigua, desde `desde`."""
        ...

    def marcar_leidas(self, destinatario: str, desde: datetime) -> int:
        """Marca las no leídas desde `desde`; devuelve cuántas cambió."""
        ...


class NotificacionesEnMemoria:
    """Para pruebas y para correr en local sin DynamoDB. No se comparte entre procesos."""

    def __init__(self) -> None:
        self._datos: dict[str, dict[str, Notificacion]] = {}
        self._candado = threading.Lock()

    def publicar(self, notificacion: Notificacion) -> None:
        with self._candado:
            self._datos.setdefault(notificacion.destinatario, {})[notificacion.fecha_id] = notificacion

    def listar(self, destinatario: str, desde: datetime) -> list[Notificacion]:
        with self._candado:
            todas = list(self._datos.get(destinatario, {}).values())
        return sorted((n for n in todas if n.en >= desde), key=lambda n: n.fecha_id, reverse=True)

    def marcar_leidas(self, destinatario: str, desde: datetime) -> int:
        cambiadas = 0
        with self._candado:
            for clave, n in self._datos.get(destinatario, {}).items():
                if n.en >= desde and not n.leida:
                    self._datos[destinatario][clave] = replace(n, leida=True)
                    cambiadas += 1
        return cambiadas

    def vaciar(self) -> None:
        with self._candado:
            self._datos.clear()


class NotificacionesDesactivadas:
    """Producción sin tabla configurada: la campana queda vacía en vez de romper el panel (se avisa en el log)."""

    def publicar(self, notificacion: Notificacion) -> None:
        return None

    def listar(self, destinatario: str, desde: datetime) -> list[Notificacion]:
        return []

    def marcar_leidas(self, destinatario: str, desde: datetime) -> int:
        return 0


class NotificacionesDynamoDB:
    def __init__(self, cliente: Any, tabla: str) -> None:
        self._cliente = cliente
        self._tabla = tabla

    def publicar(self, notificacion: Notificacion) -> None:
        expira = int((notificacion.en + timedelta(days=VIGENCIA_DIAS)).timestamp())
        self._cliente.put_item(
            TableName=self._tabla,
            Item={
                "destinatario": {"S": notificacion.destinatario},
                "fecha_id": {"S": notificacion.fecha_id},
                "tipo": {"S": notificacion.tipo},
                "titulo": {"S": notificacion.titulo},
                "texto": {"S": notificacion.texto},
                "enlace": {"S": notificacion.enlace},
                "leida": {"BOOL": notificacion.leida},
                "expira_en": {"N": str(expira)},
            },
        )

    def _items(self, destinatario: str, desde: datetime) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        argumentos: dict[str, Any] = {
            "TableName": self._tabla,
            "KeyConditionExpression": "destinatario = :d AND fecha_id >= :desde",
            "ExpressionAttributeValues": {
                ":d": {"S": destinatario},
                ":desde": {"S": desde.astimezone(UTC).isoformat()},
            },
            "ScanIndexForward": False,
        }
        while True:
            respuesta = self._cliente.query(**argumentos)
            items.extend(respuesta.get("Items", []))
            if "LastEvaluatedKey" not in respuesta:
                return items
            argumentos["ExclusiveStartKey"] = respuesta["LastEvaluatedKey"]

    def listar(self, destinatario: str, desde: datetime) -> list[Notificacion]:
        resultado = []
        for item in self._items(destinatario, desde):
            fecha, _, id_ = item["fecha_id"]["S"].partition("#")
            resultado.append(
                Notificacion(
                    destinatario=destinatario,
                    id=id_,
                    en=datetime.fromisoformat(fecha),
                    tipo=item["tipo"]["S"],
                    titulo=item["titulo"]["S"],
                    texto=item["texto"]["S"],
                    enlace=item["enlace"]["S"],
                    leida=item.get("leida", {}).get("BOOL", False),
                )
            )
        return resultado

    def marcar_leidas(self, destinatario: str, desde: datetime) -> int:
        cambiadas = 0
        for item in self._items(destinatario, desde):
            if item.get("leida", {}).get("BOOL", False):
                continue
            self._cliente.update_item(
                TableName=self._tabla,
                Key={"destinatario": item["destinatario"], "fecha_id": item["fecha_id"]},
                UpdateExpression="SET leida = :si",
                ExpressionAttributeValues={":si": {"BOOL": True}},
            )
            cambiadas += 1
        return cambiadas


_EN_MEMORIA = NotificacionesEnMemoria()


@lru_cache
def get_notificaciones_store() -> NotificacionesStore:
    ajustes = get_settings()
    if not ajustes.dynamodb_tabla_notificaciones:
        if ajustes.en_prod:
            logger.warning("Falta DYNAMODB_TABLA_NOTIFICACIONES: la campana del panel queda vacía")
            return NotificacionesDesactivadas()
        return _EN_MEMORIA
    cliente = boto3.client(
        "dynamodb",
        region_name=ajustes.aws_region,
        endpoint_url=ajustes.aws_endpoint_url or None,
        config=Config(retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=2, read_timeout=5),
    )
    return NotificacionesDynamoDB(cliente, ajustes.dynamodb_tabla_notificaciones)
