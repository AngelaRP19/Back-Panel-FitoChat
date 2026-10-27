"""SQL de fincas y cultivos. El cultivo hereda las coordenadas de su finca: la evaluación de alertas mide distancias."""

from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.paginacion import ParametrosPagina
from app.core.catalogos_api import ESPECIES, TIPOS_CULTIVO, normalizar_codigo
from app.infrastructure.db.models import Cultivo, CultivoEspecie, Finca
from app.modules.panel_api.cuentas.repository import normalizar_busqueda
from app.modules.panel_api.territorio_comun import ESTADOS_ACTIVOS, nombres_de_vereda, sin_tildes


@dataclass(frozen=True)
class CultivoFila:
    id: str
    id_finca: str
    nombre: str
    tipo: str
    especies: list[str]  # códigos de la API
    casos_activos: list[str]


@dataclass(frozen=True)
class FincaFila:
    id: str
    nombre: str
    vereda: str  # nombre guardado
    latitud: Decimal
    longitud: Decimal
    id_productor: str
    nombre_productor: str
    productor_activo: bool
    casos_activos: int
    cultivos: list[CultivoFila] = field(default_factory=list)


@dataclass(frozen=True)
class FiltrosFincas:
    q: str | None = None
    vereda: str | None = None
    especie: str | None = None
    tipo: str | None = None
    casos: Literal["con", "sin"] | None = None
    productor_id: str | None = None


_BASE = """
    base AS (
        SELECT f.id_finca AS id, f.nombre_finca AS nombre, f.vereda_sector_finca AS vereda,
               f.latitud_finca AS latitud, f.longitud_finca AS longitud, f.id_productor,
               p.nombre_productor, p.estado_cuenta_productor = 'ACTIVO' AS productor_activo,
               (SELECT count(*) FROM casos c
                  JOIN cultivos cu ON cu.id_cultivo = c.id_cultivo
                  JOIN estados e ON e.id_estado = c.id_estado
                 WHERE cu.id_finca = f.id_finca AND e.nombre_estado = ANY(:estados_activos)) AS casos_activos
        FROM fincas f JOIN productores p ON p.id_productor = f.id_productor
    )
"""
_COLUMNAS = "id, nombre, vereda, latitud, longitud, id_productor, nombre_productor, productor_activo, casos_activos"


def _fila(r: dict[str, object]) -> FincaFila:
    return FincaFila(
        id=str(r["id"]),
        nombre=str(r["nombre"]),
        vereda=str(r["vereda"]),
        latitud=r["latitud"],  # type: ignore[arg-type]
        longitud=r["longitud"],  # type: ignore[arg-type]
        id_productor=str(r["id_productor"]),
        nombre_productor=str(r["nombre_productor"]),
        productor_activo=bool(r["productor_activo"]),
        casos_activos=int(r["casos_activos"]),  # type: ignore[call-overload]
    )


async def cultivos_de(session: AsyncSession, ids_finca: list[str]) -> dict[str, list[CultivoFila]]:
    """Cultivos de las fincas indicadas, con sus especies y los casos activos de cada uno."""
    if not ids_finca:
        return {}
    resultado = await session.execute(
        text(
            """
            SELECT cu.id_cultivo, cu.id_finca, cu.nombre_cultivo, cu.tipo_cultivo,
                   COALESCE((SELECT array_agg(es.nombre_especie ORDER BY es.nombre_especie)
                             FROM cultivo_especie ce JOIN especies es ON es.id_especie = ce.id_especie
                             WHERE ce.id_cultivo = cu.id_cultivo), '{}') AS especies,
                   COALESCE((SELECT array_agg(c.id_caso ORDER BY c.id_caso)
                             FROM casos c JOIN estados e ON e.id_estado = c.id_estado
                             WHERE c.id_cultivo = cu.id_cultivo AND e.nombre_estado = ANY(:estados_activos)),
                            '{}') AS casos_activos
            FROM cultivos cu
            WHERE cu.id_finca = ANY(:ids)
            ORDER BY cu.id_cultivo
            """
        ),
        {"ids": ids_finca, "estados_activos": ESTADOS_ACTIVOS},
    )
    por_finca: dict[str, list[CultivoFila]] = {}
    for r in resultado.mappings():
        por_finca.setdefault(r["id_finca"], []).append(
            CultivoFila(
                id=r["id_cultivo"],
                id_finca=r["id_finca"],
                nombre=r["nombre_cultivo"],
                tipo=r["tipo_cultivo"],
                especies=[ESPECIES.a_codigo(n) for n in r["especies"]],
                casos_activos=list(r["casos_activos"]),
            )
        )
    return por_finca


async def _con_cultivos(session: AsyncSession, filas: list[FincaFila]) -> list[FincaFila]:
    cultivos = await cultivos_de(session, [f.id for f in filas])
    return [replace(f, cultivos=cultivos.get(f.id, [])) for f in filas]


async def obtener(session: AsyncSession, id_finca: str) -> FincaFila | None:
    resultado = await session.execute(
        text(f"WITH {_BASE} SELECT {_COLUMNAS} FROM base WHERE id = :id"),
        {"id": id_finca, "estados_activos": ESTADOS_ACTIVOS},
    )
    fila = resultado.mappings().first()
    return (await _con_cultivos(session, [_fila(dict(fila))]))[0] if fila else None


async def listar(
    session: AsyncSession, pagina: ParametrosPagina, filtros: FiltrosFincas
) -> tuple[list[FincaFila], int]:
    """Agrupadas por vereda y, dentro de cada una, por nombre."""
    condiciones = ["TRUE"]
    parametros: dict[str, object] = {
        "estados_activos": ESTADOS_ACTIVOS,
        "limite": pagina.page_size,
        "desplazamiento": pagina.offset,
    }
    if filtros.q and normalizar_busqueda(filtros.q):
        condiciones.append(f"{sin_tildes('nombre || chr(32) || nombre_productor')} LIKE :q")
        parametros["q"] = f"%{normalizar_busqueda(filtros.q)}%"
    if filtros.vereda:
        parametros["veredas"] = await nombres_de_vereda(session, filtros.vereda)
        condiciones.append("vereda = ANY(:veredas)")
    if filtros.especie:
        parametros["especie"] = ESPECIES.a_nombre(filtros.especie)
        condiciones.append(
            "EXISTS (SELECT 1 FROM cultivos cu JOIN cultivo_especie ce ON ce.id_cultivo = cu.id_cultivo "
            "JOIN especies es ON es.id_especie = ce.id_especie "
            "WHERE cu.id_finca = base.id AND es.nombre_especie = :especie)"
        )
    if filtros.tipo:
        parametros["tipo"] = TIPOS_CULTIVO.a_nombre(filtros.tipo)
        condiciones.append("EXISTS (SELECT 1 FROM cultivos cu WHERE cu.id_finca = base.id AND cu.tipo_cultivo = :tipo)")
    if filtros.casos == "con":
        condiciones.append("casos_activos > 0")
    elif filtros.casos == "sin":
        condiciones.append("casos_activos = 0")
    if filtros.productor_id:
        parametros["productor_id"] = filtros.productor_id
        condiciones.append("id_productor = :productor_id")

    where = " AND ".join(condiciones)
    resultado = await session.execute(
        text(
            f"WITH {_BASE} SELECT {_COLUMNAS}, count(*) OVER () AS total FROM base WHERE {where} "
            f"ORDER BY {sin_tildes('vereda')}, {sin_tildes('nombre')}, id LIMIT :limite OFFSET :desplazamiento"
        ),
        parametros,
    )
    filas = [dict(r) for r in resultado.mappings()]
    if filas:
        total = int(filas[0]["total"])
    else:  # página fuera de rango: sin filas, pero igual se informa el total
        sin_pagina = {k: v for k, v in parametros.items() if k not in ("limite", "desplazamiento")}
        total = int(
            await session.scalar(text(f"WITH {_BASE} SELECT count(*) FROM base WHERE {where}"), sin_pagina) or 0
        )
    return await _con_cultivos(session, [_fila(r) for r in filas]), total


async def productor(session: AsyncSession, id_productor: str) -> tuple[str, bool] | None:
    """(nombre, activo) del productor, o None si no existe."""
    fila = (
        await session.execute(
            text(
                "SELECT nombre_productor, estado_cuenta_productor = 'ACTIVO' FROM productores WHERE id_productor = :id"
            ),
            {"id": id_productor},
        )
    ).first()
    return (str(fila[0]), bool(fila[1])) if fila else None


def _decimal(valor: float) -> Decimal:
    return Decimal(str(valor)).quantize(Decimal("0.000001"))


async def crear(
    session: AsyncSession, nombre: str, vereda: str, latitud: float, longitud: float, id_productor: str
) -> str:
    finca = Finca(
        nombre_finca=nombre,
        vereda_sector_finca=vereda,
        latitud_finca=_decimal(latitud),
        longitud_finca=_decimal(longitud),
        id_productor=id_productor,
    )
    session.add(finca)
    await session.flush()
    return finca.id_finca


async def actualizar(
    session: AsyncSession,
    id_finca: str,
    *,
    nombre: str | None,
    vereda: str | None,
    latitud: float | None,
    longitud: float | None,
) -> None:
    """Si cambian las coordenadas, los cultivos de la finca las siguen (comparten ubicación)."""
    await session.execute(
        text(
            "UPDATE fincas SET nombre_finca = COALESCE(:nombre, nombre_finca), "
            "vereda_sector_finca = COALESCE(:vereda, vereda_sector_finca), "
            "latitud_finca = COALESCE(:latitud, latitud_finca), longitud_finca = COALESCE(:longitud, longitud_finca) "
            "WHERE id_finca = :id"
        ),
        {
            "id": id_finca,
            "nombre": nombre,
            "vereda": vereda,
            "latitud": None if latitud is None else _decimal(latitud),
            "longitud": None if longitud is None else _decimal(longitud),
        },
    )
    if latitud is not None or longitud is not None:
        await session.execute(
            text(
                "UPDATE cultivos c SET latitud_cultivo = f.latitud_finca, longitud_cultivo = f.longitud_finca "
                "FROM fincas f WHERE f.id_finca = c.id_finca AND c.id_finca = :id"
            ),
            {"id": id_finca},
        )


async def _ids_especies(session: AsyncSession, codigos: list[str]) -> list[str]:
    resultado = await session.execute(
        text("SELECT id_especie FROM especies WHERE nombre_especie = ANY(:nombres) ORDER BY id_especie"),
        {"nombres": [ESPECIES.a_nombre(c) for c in codigos]},
    )
    return [fila[0] for fila in resultado]


async def obtener_cultivo(session: AsyncSession, id_cultivo: str) -> CultivoFila | None:
    id_finca = await session.scalar(text("SELECT id_finca FROM cultivos WHERE id_cultivo = :id"), {"id": id_cultivo})
    if id_finca is None:
        return None
    return next((c for c in (await cultivos_de(session, [id_finca]))[id_finca] if c.id == id_cultivo), None)


async def nombre_de_cultivo_en_uso(
    session: AsyncSession, id_finca: str, nombre: str, excluir_id: str | None = None
) -> bool:
    return bool(
        await session.scalar(
            text(
                f"SELECT EXISTS (SELECT 1 FROM cultivos WHERE id_finca = :finca "
                f"AND id_cultivo IS DISTINCT FROM :excluir "
                f"AND {sin_tildes('nombre_cultivo')} = :nombre)"
            ),
            {"finca": id_finca, "excluir": excluir_id, "nombre": normalizar_busqueda(nombre)},
        )
    )


async def crear_cultivo(session: AsyncSession, finca: FincaFila, nombre: str, tipo: str, especies: list[str]) -> str:
    cultivo = Cultivo(
        nombre_cultivo=nombre,
        latitud_cultivo=finca.latitud,
        longitud_cultivo=finca.longitud,
        id_finca=finca.id,
        tipo_cultivo=TIPOS_CULTIVO.a_nombre(tipo),
    )
    session.add(cultivo)
    await session.flush()
    for id_especie in await _ids_especies(session, especies):
        session.add(CultivoEspecie(id_cultivo=cultivo.id_cultivo, id_especie=id_especie))
    await session.flush()
    return cultivo.id_cultivo


async def casos_por_especie(session: AsyncSession, id_cultivo: str, especies: list[str]) -> dict[str, list[str]]:
    """Casos (de cualquier estado) que usan cada una de las especies: el par (cultivo, especie) no se puede quitar."""
    if not especies:
        return {}
    resultado = await session.execute(
        text(
            "SELECT es.nombre_especie, c.id_caso FROM casos c JOIN especies es ON es.id_especie = c.id_especie "
            "WHERE c.id_cultivo = :cultivo AND es.nombre_especie = ANY(:nombres) ORDER BY c.id_caso"
        ),
        {"cultivo": id_cultivo, "nombres": [ESPECIES.a_nombre(e) for e in especies]},
    )
    por_especie: dict[str, list[str]] = {}
    for nombre, id_caso in resultado:
        por_especie.setdefault(ESPECIES.a_codigo(nombre), []).append(id_caso)
    return por_especie


async def actualizar_cultivo(
    session: AsyncSession,
    cultivo: CultivoFila,
    *,
    nombre: str | None,
    tipo: str | None,
    agregar: list[str],
    quitar: list[str],
) -> None:
    await session.execute(
        text(
            "UPDATE cultivos SET nombre_cultivo = COALESCE(:nombre, nombre_cultivo), "
            "tipo_cultivo = COALESCE(:tipo, tipo_cultivo) WHERE id_cultivo = :id"
        ),
        {"id": cultivo.id, "nombre": nombre, "tipo": TIPOS_CULTIVO.a_nombre(tipo) if tipo else None},
    )
    if quitar:
        await session.execute(
            text(
                "DELETE FROM cultivo_especie WHERE id_cultivo = :id AND id_especie IN "
                "(SELECT id_especie FROM especies WHERE nombre_especie = ANY(:nombres))"
            ),
            {"id": cultivo.id, "nombres": [ESPECIES.a_nombre(e) for e in quitar]},
        )
    for id_especie in await _ids_especies(session, agregar):
        session.add(CultivoEspecie(id_cultivo=cultivo.id, id_especie=id_especie))
    await session.flush()


def vereda_a_codigo(nombre: str) -> str:
    return normalizar_codigo(nombre)
