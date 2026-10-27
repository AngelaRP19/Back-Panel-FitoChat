from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import Rol
from app.infrastructure.db.models import LiderTecnico, Tecnico

ESTADO_ACTIVO = "ACTIVO"


@dataclass(frozen=True)
class CuentaPanel:
    """Cuenta del panel (técnico o líder) tal como la necesita la autenticación."""

    id: str
    nombre: str
    correo: str
    celular_e164: str
    rol: Rol
    activo: bool


async def buscar_cuenta_por_sub(session: AsyncSession, cognito_sub: str) -> CuentaPanel | None:
    """Busca el `cognito_sub` primero en `tecnicos` y luego en `lideres_tecnicos` (guía §7)."""
    tecnico = (await session.execute(select(Tecnico).where(Tecnico.cognito_sub == cognito_sub))).scalar_one_or_none()
    if tecnico is not None:
        return CuentaPanel(
            id=tecnico.id_tecnico,
            nombre=tecnico.nombre_tecnico,
            correo=tecnico.correo_tecnico,
            celular_e164=tecnico.celular_tecnico,
            rol=Rol.TECNICO,
            activo=tecnico.estado_cuenta_tecnico == ESTADO_ACTIVO,
        )
    lider = (
        await session.execute(select(LiderTecnico).where(LiderTecnico.cognito_sub == cognito_sub))
    ).scalar_one_or_none()
    if lider is not None:
        return CuentaPanel(
            id=lider.id_lider_tecnico,
            nombre=lider.nombre_lider_tecnico,
            correo=lider.correo_lider_tecnico,
            celular_e164=lider.celular_lider_tecnico,
            rol=Rol.LIDER_TECNICO,
            activo=lider.estado_cuenta_lider_tecnico == ESTADO_ACTIVO,
        )
    return None
