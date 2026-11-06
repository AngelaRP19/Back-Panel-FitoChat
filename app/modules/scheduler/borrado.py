"""Borrado físico de un caso (guía §6.7): las filas, las fotos y todas las versiones de las fotos en S3.

El orden protege los datos: primero se borra en S3 y solo si todo salió bien se borran las filas. Si S3 falla, la
excepción deshace la transacción y el caso queda intacto para el siguiente intento. La auditoría que queda no lleva
datos personales: solo el id del caso, la causa y la fecha.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.infrastructure.db import procedimientos as sp
from app.infrastructure.documentos_store import PREFIJO_S3 as PREFIJO_DOCUMENTOS
from app.infrastructure.s3_storage_client import S3StorageClient


@dataclass(frozen=True)
class Borrado:
    fotos: int
    versiones_s3: int


_POR_CASO = "(SELECT id_diagnostico FROM diagnosticos WHERE id_caso = :c)"


def borrar_caso(
    session: Session, s3: S3StorageClient, id_caso: str, ahora: datetime, accion: str, motivo: str
) -> Borrado:
    """Borra el caso y todo lo que cuelga de él. Quien llama abre y cierra la transacción (y ya bloqueó la fila)."""
    claves = list(
        session.scalars(
            text(
                "SELECT f.ruta_fotografia FROM fotografias f JOIN respuestas r ON r.id_respuesta = f.id_respuesta "
                f"WHERE r.id_diagnostico IN {_POR_CASO}"
            ),
            {"c": id_caso},
        )
    )
    # La documentación fitosanitaria (`documentos/`, B-07) comparte el bucket: nunca se borra con un caso.
    claves = [c for c in claves if not c.startswith(PREFIJO_DOCUMENTOS)]
    versiones = sum(
        s3.eliminar_todas_las_versiones(clave) for clave in dict.fromkeys(claves)
    )  # si falla, no se borra nada

    identificacion = session.scalar(
        text("SELECT id_identificacion_preliminar FROM casos WHERE id_caso = :c"), {"c": id_caso}
    )
    parametros = {"c": id_caso}
    for sentencia in (
        "DELETE FROM alerta_casos WHERE id_caso = :c",
        "DELETE FROM fotografias WHERE id_respuesta IN "
        f"(SELECT id_respuesta FROM respuestas WHERE id_diagnostico IN {_POR_CASO})",
        f"DELETE FROM respuestas WHERE id_diagnostico IN {_POR_CASO}",
        "DELETE FROM diagnosticos WHERE id_caso = :c",
        "DELETE FROM eventos WHERE id_caso = :c",
        "DELETE FROM asignaciones WHERE id_caso = :c",
        "DELETE FROM casos WHERE id_caso = :c",
    ):
        session.execute(text(sentencia), parametros)
    if identificacion is not None:
        for sentencia in (
            "DELETE FROM coincidencias_categoria WHERE id_identificacion = :i",
            "DELETE FROM identificacion_preliminar WHERE id_identificacion = :i",
        ):
            session.execute(text(sentencia), {"i": identificacion})

    session.execute(
        sp.REGISTRAR_AUDITORIA,
        {
            "origen": "SISTEMA",
            "id_actor": None,
            "accion": accion,
            "entidad": "casos",
            "descripcion": f"Caso {id_caso} eliminado: {motivo}",
            "valor_anterior": None,
            "valor_nuevo": None,
            "resultado": "EXITO",
            "fecha": ahora,
        },
    )
    return Borrado(fotos=len(claves), versiones_s3=versiones)
