"""Auditoría de las acciones del panel (D-09): la escribe solo el backend y nunca guarda teléfonos completos."""

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import Rol
from app.core.security_cognito import UsuarioActual
from app.infrastructure.db import procedimientos as sp

_ORIGEN = {Rol.LIDER_TECNICO: "LIDER", Rol.TECNICO: "TECNICO"}


async def auditar(
    session: AsyncSession,
    actor: UsuarioActual,
    accion: str,
    entidad: str,
    descripcion: str,
    anterior: dict[str, Any] | None = None,
    nuevo: dict[str, Any] | None = None,
) -> None:
    """Registra la acción en la misma transacción que el cambio: si el cambio se deshace, la auditoría también."""
    await session.execute(
        sp.REGISTRAR_AUDITORIA,
        {
            "origen": _ORIGEN[actor.rol],
            "id_actor": actor.id,
            "accion": accion,
            "entidad": entidad,
            "descripcion": descripcion,
            "valor_anterior": json.dumps(anterior, ensure_ascii=False) if anterior else None,
            "valor_nuevo": json.dumps(nuevo, ensure_ascii=False) if nuevo else None,
            "resultado": "EXITO",
            "fecha": None,
        },
    )
