"""Piezas que comparten productores, fincas y la consulta del técnico: veredas, casos activos y búsqueda."""

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.catalogos_api import ESTADOS_CASO, EstadoCaso, normalizar_codigo

# Un caso está activo para el productor y la finca mientras espera técnico, se revisa o ya tiene diagnóstico.
ESTADOS_ACTIVOS: list[str] = [
    ESTADOS_CASO.a_nombre(e) for e in (EstadoCaso.PENDIENTE_REVISION, EstadoCaso.EN_REVISION, EstadoCaso.REGISTRADO)
]
ESTADOS_ABIERTOS_EXCLUIDOS: list[str] = [ESTADOS_CASO.a_nombre(e) for e in (EstadoCaso.CERRADO, EstadoCaso.DESCARTADO)]

# Para buscar sin importar tildes ni mayúsculas (la base no tiene `unaccent`): se aplica a la columna y al texto.
SIN_TILDES = "translate(lower({}), 'áéíóúüñ', 'aeiouun')"


def sin_tildes(columna: str) -> str:
    return SIN_TILDES.format(columna)


def nombre_de_vereda(escrito: str) -> str:
    """Nombre que se guarda: lo escrito sin espacios de más; un código en mayúsculas (`SAN_LUIS`) pasa a «San Luis»."""
    limpio = " ".join(escrito.replace("_", " ").split())
    return limpio.title() if limpio == limpio.upper() else limpio


async def veredas_registradas(session: AsyncSession) -> dict[str, str]:
    """`{código: nombre}` de las veredas con fincas (no hay catálogo: son los valores distintos, guía §6.2)."""
    resultado = await session.execute(text("SELECT DISTINCT vereda_sector_finca FROM fincas"))
    return {normalizar_codigo(nombre): nombre for (nombre,) in resultado if normalizar_codigo(nombre)}


async def nombres_de_vereda(session: AsyncSession, codigo: str) -> list[str]:
    """Valores guardados que corresponden al código pedido (`SAN_LUIS` -> `San Luis`); vacío si no hay ninguno."""
    codigo = normalizar_codigo(codigo)
    resultado = await session.execute(text("SELECT DISTINCT vereda_sector_finca FROM fincas"))
    return [nombre for (nombre,) in resultado if normalizar_codigo(nombre) == codigo]


def solo_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto)
