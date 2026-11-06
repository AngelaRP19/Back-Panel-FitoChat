"""Ejecuta un job programado: `python -m app.jobs.run <nombre>`.

Código de salida (lo leen EventBridge Scheduler y CloudWatch):
- 0: terminó bien, o se omitió porque otra ejecución del mismo job seguía corriendo.
- 1: hubo errores (algún caso falló o el job lanzó una excepción): conviene revisar los logs.
- 2: el comando o la configuración están mal (nombre desconocido, falta el bucket de S3, WhatsApp no disponible…).
"""

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import create_engine

import app.infrastructure.db.models  # noqa: F401  (modelos y generador de ids, como en la API)
from app.core.config import get_database_settings, get_settings
from app.core.logging import configure_logging
from app.infrastructure.s3_storage_client import S3StorageClient, build_boto3_s3_client
from app.infrastructure.whatsapp_client import WhatsAppClient, get_whatsapp_client
from app.modules.scheduler.jobs import JOBS, NECESITAN_S3, NECESITAN_WHATSAPP, Contexto, ejecutar

logger = logging.getLogger("app.jobs")

OK, ERROR, CONFIGURACION = 0, 1, 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.jobs.run", description="Jobs programados del panel ANUC")
    parser.add_argument("nombre", choices=sorted(JOBS), help="job a ejecutar")
    args = parser.parse_args(argv)

    configure_logging("INFO")
    settings = get_settings()
    s3: S3StorageClient | None = None
    whatsapp: WhatsAppClient | None = None
    if args.nombre in NECESITAN_S3:
        if not settings.s3_bucket_evidencias:
            print("Falta S3_BUCKET_EVIDENCIAS", file=sys.stderr)
            return CONFIGURACION
        s3 = S3StorageClient(build_boto3_s3_client(), settings.s3_bucket_evidencias)
    if args.nombre in NECESITAN_WHATSAPP:
        try:
            whatsapp = get_whatsapp_client()
        except RuntimeError as error:
            print(str(error), file=sys.stderr)
            return CONFIGURACION

    engine = create_engine(get_database_settings().sqlalchemy_url)
    try:
        resultado = ejecutar(args.nombre, engine, Contexto(ahora=datetime.now(UTC), s3=s3, whatsapp=whatsapp))
    except Exception:
        logger.exception("El job terminó con una excepción", extra={"job": args.nombre})
        return ERROR
    finally:
        engine.dispose()

    logger.info(
        "Job terminado",
        extra={
            "job": resultado.nombre,
            "procesados": resultado.procesados,
            "exitosos": resultado.exitosos,
            "omitidos": resultado.omitidos,
            "errores": resultado.errores,
            "ya_en_ejecucion": resultado.ya_en_ejecucion,
            "detalle": resultado.detalle,
        },
    )
    return OK if resultado.exito else ERROR


if __name__ == "__main__":
    raise SystemExit(main())
