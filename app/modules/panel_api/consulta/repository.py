"""SQL de la consulta del técnico: solo productores, fincas y cultivos de SUS casos (P-07), filtrado en la base.

«Sus casos» son los de su asignación vigente (`asignaciones.fecha_fin IS NULL`). Todo parte de ese conjunto, así que
no hay forma de que una consulta devuelva un productor ajeno: lo que no está en sus casos no existe para él.
"""

import re
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.paginacion import ParametrosPagina
from app.core.catalogos_api import ESPECIES, ESTADOS_CASO
from app.modules.panel_api.cuentas.repository import normalizar_busqueda
from app.modules.panel_api.territorio_comun import ESTADOS_ABIERTOS_EXCLUIDOS, nombres_de_vereda, sin_tildes

SEPARADOR_ID = "__"


@dataclass(frozen=True)
class CultivoConsulta:
    id: str
    nombre: str
    especies: list[str]


@dataclass(frozen=True)
class CasoConsulta:
    id: str
    estado: str  # código de la API


@dataclass(frozen=True)
class RegistroFila:
    id_productor: str
    nombre_productor: str
    celular_e164: str
    id_finca: str
    nombre_finca: str
    vereda: str
    latitud: Decimal
    longitud: Decimal
    cultivos: list[CultivoConsulta] = field(default_factory=list)
    casos: list[CasoConsulta] = field(default_factory=list)

    @property
    def id(self) -> str:
        return f"{self.id_productor}{SEPARADOR_ID}{self.id_finca}"


@dataclass(frozen=True)
class ResumenFila:
    productores: int
    fincas: int
    casos: int


@dataclass(frozen=True)
class FiltrosConsulta:
    q: str | None = None
    vereda: str | None = None
    especie: str | None = None
    estado_casos: Literal["pendientes", "cerrados"] | None = None


_MIOS = """
    mios AS (
        SELECT c.id_caso, c.id_cultivo, e.nombre_estado, cu.id_finca, f.id_productor
        FROM asignaciones a
        JOIN casos c ON c.id_caso = a.id_caso
        JOIN estados e ON e.id_estado = c.id_estado
        JOIN cultivos cu ON cu.id_cultivo = c.id_cultivo
        JOIN fincas f ON f.id_finca = cu.id_finca
        WHERE a.id_tecnico = :tecnico AND a.fecha_fin IS NULL
    ),
    registros AS (
        SELECT g.id_productor, g.id_finca, g.n_casos, g.abiertos,
               p.nombre_productor, p.telefono_productor, f.nombre_finca, f.vereda_sector_finca,
               f.latitud_finca, f.longitud_finca
        FROM (
            SELECT m.id_productor, m.id_finca, count(*) AS n_casos,
                   bool_or(m.nombre_estado <> ALL(:cerrados)) AS abiertos
            FROM mios m GROUP BY m.id_productor, m.id_finca
        ) g
        JOIN productores p ON p.id_productor = g.id_productor
        JOIN fincas f ON f.id_finca = g.id_finca
    )
"""
_COLUMNAS = (
    "id_productor, id_finca, nombre_productor, telefono_productor, nombre_finca, vereda_sector_finca, "
    "latitud_finca, longitud_finca"
)


def _registro(r: dict[str, object]) -> RegistroFila:
    return RegistroFila(
        id_productor=str(r["id_productor"]),
        nombre_productor=str(r["nombre_productor"]),
        celular_e164=str(r["telefono_productor"]),
        id_finca=str(r["id_finca"]),
        nombre_finca=str(r["nombre_finca"]),
        vereda=str(r["vereda_sector_finca"]),
        latitud=r["latitud_finca"],  # type: ignore[arg-type]
        longitud=r["longitud_finca"],  # type: ignore[arg-type]
    )


async def _con_detalle(session: AsyncSession, id_tecnico: str, filas: list[RegistroFila]) -> list[RegistroFila]:
    """Cultivos y casos de cada registro, siempre desde los casos del técnico."""
    if not filas:
        return filas
    parametros = {"tecnico": id_tecnico, "cerrados": ESTADOS_ABIERTOS_EXCLUIDOS, "fincas": [f.id_finca for f in filas]}
    cultivos = await session.execute(
        text(
            f"""
            WITH {_MIOS}
            SELECT DISTINCT m.id_finca, cu.id_cultivo, cu.nombre_cultivo,
                   COALESCE((SELECT array_agg(es.nombre_especie ORDER BY es.nombre_especie)
                             FROM cultivo_especie ce JOIN especies es ON es.id_especie = ce.id_especie
                             WHERE ce.id_cultivo = cu.id_cultivo), '{{}}') AS especies
            FROM mios m JOIN cultivos cu ON cu.id_cultivo = m.id_cultivo
            WHERE m.id_finca = ANY(:fincas)
            ORDER BY cu.id_cultivo
            """
        ),
        parametros,
    )
    por_finca_cultivos: dict[str, list[CultivoConsulta]] = {}
    for r in cultivos.mappings():
        por_finca_cultivos.setdefault(r["id_finca"], []).append(
            CultivoConsulta(r["id_cultivo"], r["nombre_cultivo"], [ESPECIES.a_codigo(n) for n in r["especies"]])
        )
    casos = await session.execute(
        text(
            f"WITH {_MIOS} SELECT id_finca, id_caso, nombre_estado FROM mios "
            "WHERE id_finca = ANY(:fincas) ORDER BY id_caso DESC"
        ),
        parametros,
    )
    por_finca_casos: dict[str, list[CasoConsulta]] = {}
    for r in casos.mappings():
        por_finca_casos.setdefault(r["id_finca"], []).append(
            CasoConsulta(r["id_caso"], ESTADOS_CASO.a_codigo(r["nombre_estado"]))
        )
    return [
        replace(f, cultivos=por_finca_cultivos.get(f.id_finca, []), casos=por_finca_casos.get(f.id_finca, []))
        for f in filas
    ]


async def resumen(session: AsyncSession, id_tecnico: str) -> ResumenFila:
    """Totales de todo lo que el técnico tiene asignado, sin filtros."""
    fila = (
        await session.execute(
            text(f"WITH {_MIOS} SELECT count(DISTINCT id_productor), count(DISTINCT id_finca), count(*) FROM mios"),
            {"tecnico": id_tecnico, "cerrados": ESTADOS_ABIERTOS_EXCLUIDOS},
        )
    ).one()
    return ResumenFila(int(fila[0]), int(fila[1]), int(fila[2]))


async def listar(
    session: AsyncSession, id_tecnico: str, pagina: ParametrosPagina, filtros: FiltrosConsulta
) -> tuple[list[RegistroFila], int]:
    condiciones = ["TRUE"]
    parametros: dict[str, object] = {"tecnico": id_tecnico, "cerrados": ESTADOS_ABIERTOS_EXCLUIDOS}
    texto = (filtros.q or "").strip().removeprefix("#")
    if normalizar_busqueda(texto):
        parametros["q"] = f"%{normalizar_busqueda(texto)}%"
        por_texto = f"{sin_tildes('nombre_productor || chr(32) || nombre_finca')} LIKE :q"
        if re.fullmatch(r"\d{1,8}", texto):  # un número también se busca en el código del caso (#0241)
            parametros["numero"] = f"%{texto}%"
            por_texto += (
                " OR EXISTS (SELECT 1 FROM mios m WHERE m.id_productor = registros.id_productor "
                "AND m.id_finca = registros.id_finca "
                "AND lpad(ltrim(substr(m.id_caso, 5), '0'), 4, '0') LIKE :numero)"
            )
        condiciones.append(f"({por_texto})")
    if filtros.vereda:
        parametros["veredas"] = await nombres_de_vereda(session, filtros.vereda)
        condiciones.append("vereda_sector_finca = ANY(:veredas)")
    if filtros.especie:
        parametros["especie"] = ESPECIES.a_nombre(filtros.especie)
        condiciones.append(
            "EXISTS (SELECT 1 FROM mios m JOIN cultivo_especie ce ON ce.id_cultivo = m.id_cultivo "
            "JOIN especies es ON es.id_especie = ce.id_especie "
            "WHERE m.id_finca = registros.id_finca AND es.nombre_especie = :especie)"
        )
    if filtros.estado_casos == "pendientes":
        condiciones.append("abiertos")
    elif filtros.estado_casos == "cerrados":
        condiciones.append("NOT abiertos")
    where = " AND ".join(condiciones)

    resultado = await session.execute(
        text(
            f"WITH {_MIOS} SELECT {_COLUMNAS}, count(*) OVER () AS total FROM registros WHERE {where} "
            f"ORDER BY {sin_tildes('nombre_productor')}, {sin_tildes('nombre_finca')}, id_finca "
            "LIMIT :limite OFFSET :desplazamiento"
        ),
        {**parametros, "limite": pagina.page_size, "desplazamiento": pagina.offset},
    )
    filas = [dict(r) for r in resultado.mappings()]
    if filas:
        total = int(filas[0]["total"])
    else:
        total = int(
            await session.scalar(text(f"WITH {_MIOS} SELECT count(*) FROM registros WHERE {where}"), parametros) or 0
        )
    return await _con_detalle(session, id_tecnico, [_registro(r) for r in filas]), total


async def obtener(session: AsyncSession, id_tecnico: str, id_registro: str) -> RegistroFila | None:
    """Un registro `PRO-…__FIN-…`; si no está en los casos del técnico, no existe."""
    id_productor, separador, id_finca = id_registro.partition(SEPARADOR_ID)
    if not separador or not id_productor or not id_finca:
        return None
    resultado = await session.execute(
        text(
            f"WITH {_MIOS} SELECT {_COLUMNAS} FROM registros "
            "WHERE id_productor = :id_productor AND id_finca = :id_finca"
        ),
        {
            "tecnico": id_tecnico,
            "cerrados": ESTADOS_ABIERTOS_EXCLUIDOS,
            "id_productor": id_productor,
            "id_finca": id_finca,
        },
    )
    fila = resultado.mappings().first()
    return (await _con_detalle(session, id_tecnico, [_registro(dict(fila))]))[0] if fila else None
