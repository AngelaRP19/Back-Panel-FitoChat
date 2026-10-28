"""Notificaciones de la campana (B-10 · D-20): quién recibe cada aviso y cómo se lee.

Los servicios llaman a `Notificador` **después** de confirmar su transacción de PostgreSQL, para no avisar de algo que
se revirtió. Si DynamoDB falla, se registra en el log y la operación sigue: un aviso perdido no es un error del caso.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.notificaciones_store import (
    Notificacion,
    NotificacionesStore,
    desde_visibles,
    get_notificaciones_store,
    nuevo_id,
)

logger = logging.getLogger(__name__)

# Tipos de aviso (el panel elige el icono por el tipo).
CASO_SIN_TECNICO = "CASO_SIN_TECNICO"
CASO_ASIGNADO = "CASO_ASIGNADO"
DIAGNOSTICO_REGISTRADO = "DIAGNOSTICO_REGISTRADO"
ALERTA_GENERADA = "ALERTA_GENERADA"
ALERTA_CONFIRMADA = "ALERTA_CONFIRMADA"
ALERTA_COMUNICADA = "ALERTA_COMUNICADA"
ALERTA_CORREGIDA = "ALERTA_CORREGIDA"
PROXIMOS_A_VENCER = "PROXIMOS_A_VENCER"


LIDERES_ACTIVOS_SQL = text("SELECT id_lider_tecnico FROM lideres_tecnicos WHERE estado_cuenta_lider_tecnico = 'ACTIVO'")


async def lideres_activos(session: AsyncSession) -> list[str]:
    resultado = await session.execute(LIDERES_ACTIVOS_SQL)
    return [str(i) for (i,) in resultado]


async def seguidores_de_alerta(session: AsyncSession, id_alerta: str) -> list[str]:
    """Técnicos cuya última acción sobre la alerta fue «seguir» (P-06: el aviso es la notificación)."""
    resultado = await session.execute(
        text(
            """
            SELECT id_tecnico FROM (
                SELECT DISTINCT ON (id_tecnico) id_tecnico, accion
                FROM auditoria
                WHERE id_tecnico IS NOT NULL AND accion IN ('SEGUIR_ALERTA', 'DEJAR_SEGUIR') AND descripcion LIKE :d
                ORDER BY id_tecnico, fecha_auditoria DESC, id_auditoria DESC
            ) ultima
            WHERE accion = 'SEGUIR_ALERTA'
            """
        ),
        {"d": f"%{id_alerta}%"},
    )
    return [str(i) for (i,) in resultado]


class Notificador:
    def __init__(self, store: NotificacionesStore | None = None) -> None:
        self.store = store or get_notificaciones_store()

    async def avisar(self, destinatarios: Iterable[str], tipo: str, titulo: str, texto: str, enlace: str) -> None:
        """Una notificación por destinatario (sin repetir). Nunca lanza: los errores quedan en el log."""
        en = datetime.now(UTC)
        for destinatario in dict.fromkeys(destinatarios):
            notificacion = Notificacion(
                destinatario=destinatario, id=nuevo_id(), en=en, tipo=tipo, titulo=titulo, texto=texto, enlace=enlace
            )
            try:
                await run_in_threadpool(self.store.publicar, notificacion)
            except Exception:  # noqa: BLE001 - un aviso perdido no debe tumbar la operación que lo originó
                logger.exception("No se pudo guardar la notificación %s para %s", tipo, destinatario)

    async def listar(self, destinatario: str) -> list[Notificacion]:
        return await run_in_threadpool(self.store.listar, destinatario, desde_visibles())

    async def contar_nuevas(self, destinatario: str) -> int:
        """Para el contador del Sidebar: si DynamoDB falla, 0 (el panel no se cae por la campana)."""
        try:
            return sum(not n.leida for n in await self.listar(destinatario))
        except Exception:  # noqa: BLE001
            logger.exception("No se pudieron contar las notificaciones de %s", destinatario)
            return 0

    async def marcar_leidas(self, destinatario: str) -> int:
        return await run_in_threadpool(self.store.marcar_leidas, destinatario, desde_visibles())


@dataclass(frozen=True)
class Aviso:
    """Aviso que se publica después del commit (los procesos síncronos, como el consumidor o los jobs)."""

    destinatarios: tuple[str, ...]
    tipo: str
    titulo: str
    texto: str
    enlace: str


def publicar_sync(aviso: Aviso, store: NotificacionesStore | None = None) -> None:
    """Versión síncrona de `Notificador.avisar`. Nunca lanza: los errores quedan en el log."""
    store = store or get_notificaciones_store()
    en = datetime.now(UTC)
    for destinatario in dict.fromkeys(aviso.destinatarios):
        try:
            store.publicar(
                Notificacion(destinatario, nuevo_id(), en, aviso.tipo, aviso.titulo, aviso.texto, aviso.enlace)
            )
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo guardar la notificación %s para %s", aviso.tipo, destinatario)


def get_notificador() -> Notificador:
    return Notificador()
