"""Consultas guardadas de los reportes (D-19): DynamoDB `anuc-consultas-reportes`, sin envío programado (D-21).

Tabla con clave de partición `id_consulta` (`CNS-…`, generado aquí con un sufijo aleatorio). Son pocas (decenas), así
que se listan con Scan y se filtran en memoria por visibilidad. Los filtros se guardan como un mapa de texto, igual que
viajan en la URL del panel. Detrás de una interfaz, con una versión en memoria para pruebas y local.
"""

import logging
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from typing import Any, Protocol

import boto3
from botocore.config import Config

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def nuevo_id() -> str:
    return f"CNS-{secrets.token_hex(5).upper()}"


@dataclass(frozen=True)
class Consulta:
    id: str
    nombre: str
    tipo: str  # casos, identificacion, alertas, productores o tecnicos
    descripcion: str
    filtros: dict[str, str] = field(default_factory=dict)
    visibilidad: str = "equipo"  # yo o equipo
    creada_por: str = ""  # id del líder
    creada_por_nombre: str = ""
    creada_en: datetime | None = None


class ConsultasStore(Protocol):
    def guardar(self, consulta: Consulta) -> None: ...

    def listar(self) -> list[Consulta]: ...

    def obtener(self, id_consulta: str) -> Consulta | None: ...

    def borrar(self, id_consulta: str) -> None: ...


class ConsultasEnMemoria:
    def __init__(self) -> None:
        self._datos: dict[str, Consulta] = {}
        self._candado = threading.Lock()

    def guardar(self, consulta: Consulta) -> None:
        with self._candado:
            self._datos[consulta.id] = consulta

    def listar(self) -> list[Consulta]:
        with self._candado:
            return list(self._datos.values())

    def obtener(self, id_consulta: str) -> Consulta | None:
        return self._datos.get(id_consulta)

    def borrar(self, id_consulta: str) -> None:
        with self._candado:
            self._datos.pop(id_consulta, None)

    def vaciar(self) -> None:
        with self._candado:
            self._datos.clear()


class ConsultasDesactivadas:
    """Producción sin tabla: guardar falla con un mensaje claro y la lista queda vacía."""

    def guardar(self, consulta: Consulta) -> None:
        raise RuntimeError("Falta DYNAMODB_TABLA_CONSULTAS")

    def listar(self) -> list[Consulta]:
        return []

    def obtener(self, id_consulta: str) -> Consulta | None:
        return None

    def borrar(self, id_consulta: str) -> None:
        return None


class ConsultasDynamoDB:
    def __init__(self, cliente: Any, tabla: str) -> None:
        self._cliente = cliente
        self._tabla = tabla

    @staticmethod
    def _a_item(c: Consulta) -> dict[str, Any]:
        item: dict[str, Any] = {
            "id_consulta": {"S": c.id},
            "nombre": {"S": c.nombre},
            "tipo": {"S": c.tipo},
            "descripcion": {"S": c.descripcion},
            "filtros": {"M": {k: {"S": v} for k, v in c.filtros.items()}},
            "visibilidad": {"S": c.visibilidad},
            "creada_por": {"S": c.creada_por},
            "creada_por_nombre": {"S": c.creada_por_nombre},
        }
        if c.creada_en:
            item["creada_en"] = {"S": c.creada_en.isoformat()}
        return item

    @staticmethod
    def _de_item(item: dict[str, Any]) -> Consulta:
        creada = item.get("creada_en", {}).get("S")
        return Consulta(
            id=item["id_consulta"]["S"],
            nombre=item["nombre"]["S"],
            tipo=item["tipo"]["S"],
            descripcion=item.get("descripcion", {}).get("S", ""),
            filtros={k: v["S"] for k, v in item.get("filtros", {}).get("M", {}).items()},
            visibilidad=item.get("visibilidad", {}).get("S", "equipo"),
            creada_por=item.get("creada_por", {}).get("S", ""),
            creada_por_nombre=item.get("creada_por_nombre", {}).get("S", ""),
            creada_en=datetime.fromisoformat(creada) if creada else None,
        )

    def guardar(self, consulta: Consulta) -> None:
        self._cliente.put_item(TableName=self._tabla, Item=self._a_item(consulta))

    def listar(self) -> list[Consulta]:
        consultas: list[Consulta] = []
        argumentos: dict[str, Any] = {"TableName": self._tabla}
        while True:
            respuesta = self._cliente.scan(**argumentos)
            consultas.extend(self._de_item(i) for i in respuesta.get("Items", []))
            if "LastEvaluatedKey" not in respuesta:
                return consultas
            argumentos["ExclusiveStartKey"] = respuesta["LastEvaluatedKey"]

    def obtener(self, id_consulta: str) -> Consulta | None:
        item = self._cliente.get_item(TableName=self._tabla, Key={"id_consulta": {"S": id_consulta}}).get("Item")
        return self._de_item(item) if item else None

    def borrar(self, id_consulta: str) -> None:
        self._cliente.delete_item(TableName=self._tabla, Key={"id_consulta": {"S": id_consulta}})


_EN_MEMORIA = ConsultasEnMemoria()


@lru_cache
def get_consultas_store() -> ConsultasStore:
    ajustes = get_settings()
    if not ajustes.dynamodb_tabla_consultas:
        if ajustes.en_prod:
            logger.warning("Falta DYNAMODB_TABLA_CONSULTAS: las consultas guardadas no están disponibles")
            return ConsultasDesactivadas()
        return _EN_MEMORIA
    cliente = boto3.client(
        "dynamodb",
        region_name=ajustes.aws_region,
        endpoint_url=ajustes.aws_endpoint_url or None,
        config=Config(retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=2, read_timeout=5),
    )
    return ConsultasDynamoDB(cliente, ajustes.dynamodb_tabla_consultas)
