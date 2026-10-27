"""Evidencias en S3 (guía §6.7): URLs prefirmadas y borrado físico.

El bucket de evidencias tiene el versionado activado: un `DeleteObject` simple solo agrega un marcador de borrado y
deja la foto recuperable. Para cumplir el borrado físico, `eliminar_todas_las_versiones` lista y elimina TODAS las
versiones y marcadores del objeto. Exige `s3:DeleteObject`, `s3:DeleteObjectVersion` y `s3:ListBucketVersions`.
"""

from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import get_settings

VIGENCIA_URL_SEGUNDOS = 600  # propuesta de la guía (§7): 10 minutos
_LOTE_MAXIMO = 1000  # límite de DeleteObjects


class ErrorBorradoS3(RuntimeError):
    """S3 no pudo eliminar alguna versión; la fila de la base no debe borrarse hasta lograrlo."""


class S3StorageClient:
    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self.bucket = bucket

    def subir(self, clave: str, contenido: bytes, content_type: str = "image/webp") -> None:
        self._client.put_object(Bucket=self.bucket, Key=clave, Body=contenido, ContentType=content_type)

    def url_prefirmada(self, clave: str, expira_segundos: int = VIGENCIA_URL_SEGUNDOS) -> str:
        """URL de lectura con vigencia corta; el bucket es privado."""
        url: str = self._client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": clave}, ExpiresIn=expira_segundos
        )
        return url

    def existe(self, clave: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=clave)
        except ClientError as error:
            if error.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        return True

    def listar_versiones(self, clave: str) -> list[dict[str, Any]]:
        """Versiones y marcadores de borrado de exactamente esa clave (no de las que solo comparten prefijo)."""
        versiones: list[dict[str, Any]] = []
        for pagina in self._client.get_paginator("list_object_versions").paginate(Bucket=self.bucket, Prefix=clave):
            for elemento in [*pagina.get("Versions", []), *pagina.get("DeleteMarkers", [])]:
                if elemento["Key"] == clave:
                    versiones.append({"Key": elemento["Key"], "VersionId": elemento["VersionId"]})
        return versiones

    def eliminar_todas_las_versiones(self, clave: str) -> int:
        """Borrado físico: elimina cada versión y marcador. Devuelve cuántos eliminó; 0 si el objeto no existe."""
        versiones = self.listar_versiones(clave)
        for inicio in range(0, len(versiones), _LOTE_MAXIMO):
            respuesta = self._client.delete_objects(
                Bucket=self.bucket, Delete={"Objects": versiones[inicio : inicio + _LOTE_MAXIMO], "Quiet": True}
            )
            if respuesta.get("Errors"):
                raise ErrorBorradoS3(f"No se pudo eliminar {len(respuesta['Errors'])} versión(es) de {clave}")
        return len(versiones)


def build_boto3_s3_client() -> Any:
    """Cliente boto3 con la cadena de credenciales por defecto (rol IAM en AWS; LocalStack con `AWS_ENDPOINT_URL`)."""
    settings = get_settings()
    config = Config(
        retries={"max_attempts": 3, "mode": "standard"},
        connect_timeout=3,
        read_timeout=10,
        signature_version="s3v4",
        s3={"addressing_style": "path"} if settings.aws_endpoint_url else {},
    )
    return boto3.client("s3", region_name=settings.aws_region, endpoint_url=settings.aws_endpoint_url, config=config)


@lru_cache
def get_s3_storage_client() -> S3StorageClient:
    settings = get_settings()
    if not settings.s3_bucket_evidencias:
        raise RuntimeError("Falta S3_BUCKET_EVIDENCIAS")
    return S3StorageClient(build_boto3_s3_client(), settings.s3_bucket_evidencias)
