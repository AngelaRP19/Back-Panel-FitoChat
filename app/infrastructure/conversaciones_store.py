"""Estado de cada conversación del chatbot (D-15): un documento JSON por teléfono, en DynamoDB.

Guarda el paso actual, el caso en curso, el contexto y la última actividad. Detrás de una interfaz, con una versión en
memoria para las pruebas y para correr en local sin DynamoDB. Es un estado de trabajo: la verdad sobre el caso (sus
respuestas, fotos y estado) vive en PostgreSQL, así que perder este documento solo obliga a retomar el flujo.
"""

import json
import time
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any, Protocol

import boto3
from botocore.config import Config

from app.core.config import get_settings


@dataclass
class Conversacion:
    telefono: str  # E.164 sin «+», clave del documento
    paso: str
    caso_id: str | None = None
    contexto: dict[str, Any] = field(default_factory=dict)
    ultima_actividad: float = field(default_factory=time.time)  # segundos desde la época


class ConversacionesStore(Protocol):
    def obtener(self, telefono: str) -> Conversacion | None: ...

    def guardar(self, conversacion: Conversacion) -> None: ...

    def borrar(self, telefono: str) -> None: ...


class ConversacionesEnMemoria:
    """Para pruebas y para correr en local sin DynamoDB. No se comparte entre procesos."""

    def __init__(self) -> None:
        self._datos: dict[str, str] = {}

    def obtener(self, telefono: str) -> Conversacion | None:
        crudo = self._datos.get(telefono)
        return Conversacion(**json.loads(crudo)) if crudo else None

    def guardar(self, conversacion: Conversacion) -> None:
        self._datos[conversacion.telefono] = json.dumps(asdict(conversacion), ensure_ascii=False)

    def borrar(self, telefono: str) -> None:
        self._datos.pop(telefono, None)


class ConversacionesDynamoDB:
    """Tabla con clave de partición `telefono` (string). `expira_en` es el atributo TTL que limpia las viejas."""

    def __init__(self, cliente: Any, tabla: str, vigencia_dias: int = 7) -> None:
        self._cliente = cliente
        self._tabla = tabla
        self._vigencia = vigencia_dias * 86_400

    def obtener(self, telefono: str) -> Conversacion | None:
        item = self._cliente.get_item(TableName=self._tabla, Key={"telefono": {"S": telefono}}).get("Item")
        if not item:
            return None
        return Conversacion(
            telefono=item["telefono"]["S"],
            paso=item["paso"]["S"],
            caso_id=item.get("caso_id", {}).get("S"),
            contexto=json.loads(item.get("contexto", {}).get("S", "{}")),
            ultima_actividad=float(item["ultima_actividad"]["N"]),
        )

    def guardar(self, conversacion: Conversacion) -> None:
        item: dict[str, Any] = {
            "telefono": {"S": conversacion.telefono},
            "paso": {"S": conversacion.paso},
            "contexto": {"S": json.dumps(conversacion.contexto, ensure_ascii=False)},
            "ultima_actividad": {"N": str(conversacion.ultima_actividad)},
            "expira_en": {"N": str(int(conversacion.ultima_actividad) + self._vigencia)},
        }
        if conversacion.caso_id:
            item["caso_id"] = {"S": conversacion.caso_id}
        self._cliente.put_item(TableName=self._tabla, Item=item)

    def borrar(self, telefono: str) -> None:
        self._cliente.delete_item(TableName=self._tabla, Key={"telefono": {"S": telefono}})


_EN_MEMORIA = ConversacionesEnMemoria()


@lru_cache
def get_conversaciones_store() -> ConversacionesStore:
    ajustes = get_settings()
    if not ajustes.dynamodb_tabla_conversaciones:
        if ajustes.en_prod:
            raise RuntimeError("Falta DYNAMODB_TABLA_CONVERSACIONES")
        return _EN_MEMORIA
    cliente = boto3.client(
        "dynamodb",
        region_name=ajustes.aws_region,
        endpoint_url=ajustes.aws_endpoint_url or None,
        config=Config(retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=2, read_timeout=5),
    )
    return ConversacionesDynamoDB(cliente, ajustes.dynamodb_tabla_conversaciones, ajustes.conversacion_vigencia_dias)
