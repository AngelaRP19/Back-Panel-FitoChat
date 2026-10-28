"""Documentación fitosanitaria (D-18): metadatos en DynamoDB `anuc-documentos` y el PDF en S3 bajo `documentos/`.

Tabla con clave de partición `id_documento` (`DOC-…`, generado aquí con un sufijo aleatorio). Son pocos documentos
(decenas), así que se listan con Scan y se filtran en memoria. Detrás de una interfaz, con una versión en memoria para
pruebas y local. Los archivos van al bucket de evidencias con el prefijo `documentos/`, que los jobs de borrado no
tocan (solo borran las claves de `fotografias`). Sin bucket fuera de producción, los PDF se guardan en memoria.
"""

import logging
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any, Protocol

import boto3
from botocore.config import Config

from app.core.config import get_settings
from app.infrastructure.s3_storage_client import get_s3_storage_client

logger = logging.getLogger(__name__)

PREFIJO_S3 = "documentos/"


def nuevo_id() -> str:
    return f"DOC-{secrets.token_hex(5).upper()}"


def clave_s3(id_documento: str) -> str:
    return f"{PREFIJO_S3}{id_documento}.pdf"


@dataclass(frozen=True)
class Documento:
    id: str
    titulo: str
    tipo: str  # MANUAL, FICHA_TECNICA, GUIA_MANEJO o LIBRO_REFERENCIA
    categoria: str | None  # código de la categoría (TRIPS…) o None si es general
    clave_s3: str
    tamano_bytes: int
    subido_por: str  # id del líder
    subido_por_nombre: str
    subido_en: datetime


class DocumentosStore(Protocol):
    def guardar(self, documento: Documento) -> None: ...

    def listar(self) -> list[Documento]: ...

    def obtener(self, id_documento: str) -> Documento | None: ...


class DocumentosEnMemoria:
    def __init__(self) -> None:
        self._datos: dict[str, Documento] = {}
        self._candado = threading.Lock()

    def guardar(self, documento: Documento) -> None:
        with self._candado:
            self._datos[documento.id] = documento

    def listar(self) -> list[Documento]:
        with self._candado:
            return list(self._datos.values())

    def obtener(self, id_documento: str) -> Documento | None:
        return self._datos.get(id_documento)

    def vaciar(self) -> None:
        with self._candado:
            self._datos.clear()


class DocumentosDesactivados:
    """Producción sin tabla: subir falla con un mensaje claro y la lista queda vacía."""

    def guardar(self, documento: Documento) -> None:
        raise RuntimeError("Falta DYNAMODB_TABLA_DOCUMENTOS")

    def listar(self) -> list[Documento]:
        return []

    def obtener(self, id_documento: str) -> Documento | None:
        return None


class DocumentosDynamoDB:
    def __init__(self, cliente: Any, tabla: str) -> None:
        self._cliente = cliente
        self._tabla = tabla

    @staticmethod
    def _a_item(d: Documento) -> dict[str, Any]:
        item: dict[str, Any] = {
            "id_documento": {"S": d.id},
            "titulo": {"S": d.titulo},
            "tipo": {"S": d.tipo},
            "clave_s3": {"S": d.clave_s3},
            "tamano_bytes": {"N": str(d.tamano_bytes)},
            "subido_por": {"M": {"id": {"S": d.subido_por}, "nombre": {"S": d.subido_por_nombre}}},
            "subido_en": {"S": d.subido_en.isoformat()},
        }
        if d.categoria:
            item["categoria"] = {"S": d.categoria}
        return item

    @staticmethod
    def _de_item(item: dict[str, Any]) -> Documento:
        subido_por = item.get("subido_por", {}).get("M", {})
        return Documento(
            id=item["id_documento"]["S"],
            titulo=item["titulo"]["S"],
            tipo=item["tipo"]["S"],
            categoria=item.get("categoria", {}).get("S"),
            clave_s3=item["clave_s3"]["S"],
            tamano_bytes=int(item.get("tamano_bytes", {}).get("N", "0")),
            subido_por=subido_por.get("id", {}).get("S", ""),
            subido_por_nombre=subido_por.get("nombre", {}).get("S", ""),
            subido_en=datetime.fromisoformat(item["subido_en"]["S"]),
        )

    def guardar(self, documento: Documento) -> None:
        self._cliente.put_item(TableName=self._tabla, Item=self._a_item(documento))

    def listar(self) -> list[Documento]:
        documentos: list[Documento] = []
        argumentos: dict[str, Any] = {"TableName": self._tabla}
        while True:
            respuesta = self._cliente.scan(**argumentos)
            documentos.extend(self._de_item(i) for i in respuesta.get("Items", []))
            if "LastEvaluatedKey" not in respuesta:
                return documentos
            argumentos["ExclusiveStartKey"] = respuesta["LastEvaluatedKey"]

    def obtener(self, id_documento: str) -> Documento | None:
        item = self._cliente.get_item(TableName=self._tabla, Key={"id_documento": {"S": id_documento}}).get("Item")
        return self._de_item(item) if item else None


_EN_MEMORIA = DocumentosEnMemoria()


@lru_cache
def get_documentos_store() -> DocumentosStore:
    ajustes = get_settings()
    if not ajustes.dynamodb_tabla_documentos:
        if ajustes.en_prod:
            logger.warning("Falta DYNAMODB_TABLA_DOCUMENTOS: la documentación no está disponible")
            return DocumentosDesactivados()
        return _EN_MEMORIA
    cliente = boto3.client(
        "dynamodb",
        region_name=ajustes.aws_region,
        endpoint_url=ajustes.aws_endpoint_url or None,
        config=Config(retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=2, read_timeout=5),
    )
    return DocumentosDynamoDB(cliente, ajustes.dynamodb_tabla_documentos)


# --- archivos PDF -------------------------------------------------------------------------------------------------


class ArchivosDocumentos(Protocol):
    def subir(self, clave: str, contenido: bytes, content_type: str = ...) -> None: ...

    def descargar(self, clave: str) -> bytes: ...


class ArchivosEnMemoria:
    """Local y pruebas sin bucket: el PDF vive mientras corre el proceso."""

    def __init__(self) -> None:
        self._datos: dict[str, bytes] = {}

    def subir(self, clave: str, contenido: bytes, content_type: str = "application/pdf") -> None:
        self._datos[clave] = contenido

    def descargar(self, clave: str) -> bytes:
        try:
            return self._datos[clave]
        except KeyError:
            raise FileNotFoundError(clave) from None

    def vaciar(self) -> None:
        self._datos.clear()


_ARCHIVOS_EN_MEMORIA = ArchivosEnMemoria()


def get_archivos_documentos() -> ArchivosDocumentos:
    """El bucket de evidencias; sin bucket fuera de producción, memoria (en producción falta configuración: error)."""
    try:
        return get_s3_storage_client()
    except RuntimeError:
        if get_settings().en_prod:
            raise
        return _ARCHIVOS_EN_MEMORIA
