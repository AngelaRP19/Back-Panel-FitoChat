"""Credenciales para llegar a la cuenta de IA (guía §11, módulo 11).

Bedrock y Rekognition viven en OTRA cuenta AWS. Para llegar se asume un rol con `sts:AssumeRole` (`IA_ROLE_ARN`) y un
`ExternalId` (`IA_EXTERNAL_ID`). Las credenciales temporales de una sesión encadenada duran como máximo 1 hora, así que
se **renuevan antes de que venzan** (con un margen de 5 minutos) y los clientes se vuelven a crear con las nuevas.
"""

import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import boto3
from botocore.config import Config

from app.core.config import get_settings

MARGEN_RENOVACION_SEGUNDOS = 300
DURACION_SEGUNDOS = 3600
NOMBRE_SESION = "anuc-backend"


class ProveedorClientesIA:
    """Entrega clientes de la cuenta de IA y renueva sus credenciales antes de que venzan."""

    def __init__(
        self,
        sts: Any,
        role_arn: str,
        external_id: str,
        region: str,
        timeout_segundos: int = 30,
        reloj: Callable[[], float] = time.time,
        fabrica: Callable[..., Any] | None = None,
    ) -> None:
        self._sts = sts
        self._role_arn = role_arn
        self._external_id = external_id
        self._region = region
        self._timeout = timeout_segundos
        self._reloj = reloj
        self._fabrica = fabrica or self._cliente_boto3
        self._cerrojo = threading.Lock()
        self._credenciales: dict[str, Any] | None = None
        self._vence: float = 0.0
        self._clientes: dict[str, Any] = {}
        self.renovaciones = 0

    def _cliente_boto3(self, servicio: str, **credenciales: str) -> Any:
        return boto3.client(
            servicio,
            region_name=self._region,
            config=Config(
                connect_timeout=5, read_timeout=self._timeout, retries={"max_attempts": 1, "mode": "standard"}
            ),
            **credenciales,
        )

    def _vigentes(self) -> bool:
        return self._credenciales is not None and self._reloj() < self._vence - MARGEN_RENOVACION_SEGUNDOS

    def _renovar(self) -> None:
        respuesta = self._sts.assume_role(
            RoleArn=self._role_arn,
            RoleSessionName=NOMBRE_SESION,
            ExternalId=self._external_id,
            DurationSeconds=DURACION_SEGUNDOS,
        )
        c = respuesta["Credentials"]
        vencimiento = c["Expiration"]
        self._vence = vencimiento.timestamp() if isinstance(vencimiento, datetime) else float(vencimiento)
        self._credenciales = {
            "aws_access_key_id": c["AccessKeyId"],
            "aws_secret_access_key": c["SecretAccessKey"],
            "aws_session_token": c["SessionToken"],
        }
        self._clientes.clear()  # los clientes viejos llevan las credenciales anteriores
        self.renovaciones += 1

    def cliente(self, servicio: str) -> Any:
        """Cliente del servicio (`bedrock-runtime`, `rekognition`) con credenciales vigentes."""
        with self._cerrojo:
            if not self._vigentes():
                self._renovar()
            if servicio not in self._clientes:
                assert self._credenciales is not None
                self._clientes[servicio] = self._fabrica(servicio, **self._credenciales)
            return self._clientes[servicio]


_PROVEEDOR: ProveedorClientesIA | None = None


def get_proveedor_ia() -> ProveedorClientesIA:
    """Uno por proceso, para compartir las credenciales entre Bedrock y Rekognition."""
    global _PROVEEDOR
    if _PROVEEDOR is None:
        ajustes = get_settings()
        if not ajustes.ia_role_arn or ajustes.ia_external_id is None:
            raise RuntimeError("Faltan IA_ROLE_ARN e IA_EXTERNAL_ID")
        _PROVEEDOR = ProveedorClientesIA(
            boto3.client("sts", region_name=ajustes.aws_region),
            ajustes.ia_role_arn,
            ajustes.ia_external_id.get_secret_value(),
            ajustes.ia_region,
            ajustes.ia_timeout_segundos,
        )
    return _PROVEEDOR
