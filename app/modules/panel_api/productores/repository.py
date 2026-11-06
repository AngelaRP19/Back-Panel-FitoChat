"""SQL de los productores: listado con filtros combinados, conteos de casos y fincas resumidas."""

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.paginacion import ParametrosPagina
from app.core.catalogos_api import ESPECIES, normalizar_codigo
from app.infrastructure.db.models import Productor
from app.modules.panel_api.cuentas.repository import normalizar_busqueda
from app.modules.panel_api.territorio_comun import ESTADOS_ACTIVOS, nombres_de_vereda, sin_tildes, solo_digitos

ACTIVO = "ACTIVO"
DESHABILITADO = "DESHABILITADO"


@dataclass(frozen=True)
class FincaResumen:
    id: str
    nombre: str
    vereda: str  # código de la API
    especies: list[str]  # códigos de la API


@dataclass(frozen=True)
class ProductorFila:
    id: str
    nombre: str
    celular_e164: str
    activo: bool
    registro: date
    ultima_consulta: datetime | None
    casos_activos: int
    casos_ultimo_anio: int
    fincas: list[FincaResumen] = field(default_factory=list)


@dataclass(frozen=True)
class FiltrosProductores:
    q: str | None = None
    estado: Literal["activo", "deshabilitado"] | None = None
    vereda: str | None = None
    especie: str | None = None  # código de la API
    con_casos: Literal["con", "sin"] | None = None
    finca: str | None = None
    desde: date | None = None
    hasta: date | None = None
    consulta: Literal["30", "90"] | None = None


_BASE = """
    base AS (
        SELECT p.id_productor AS id, p.nombre_productor AS nombre, p.telefono_productor AS celular,
               p.estado_cuenta_productor = 'ACTIVO' AS activo, p.fecha_registro_productor AS registro,
               GREATEST(
                   (SELECT max(m.fecha_recepcion) FROM mensajes_whatsapp m WHERE m.id_productor = p.id_productor),
                   (SELECT max(m.fecha_recepcion) FROM mensajes_whatsapp m WHERE m.telefono = p.telefono_productor)
               ) AS ultima_consulta,
               COALESCE(k.activos, 0) AS casos_activos, COALESCE(k.ultimo_anio, 0) AS casos_ultimo_anio
        FROM productores p
        LEFT JOIN (
            SELECT f.id_productor,
                   count(*) FILTER (WHERE e.nombre_estado = ANY(:estados_activos)) AS activos,
                   count(*) FILTER (
                       WHERE COALESCE(c.fecha_envio_panel, c.fecha_creacion_caso) >= fn_ahora() - interval '365 days'
                   ) AS ultimo_anio
            FROM casos c
            JOIN cultivos cu ON cu.id_cultivo = c.id_cultivo
            JOIN fincas f ON f.id_finca = cu.id_finca
            JOIN estados e ON e.id_estado = c.id_estado
            GROUP BY f.id_productor
        ) k ON k.id_productor = p.id_productor
    )
"""
_COLUMNAS = "id, nombre, celular, activo, registro, ultima_consulta, casos_activos, casos_ultimo_anio"


def _fila(r: dict[str, object]) -> ProductorFila:
    return ProductorFila(
        id=str(r["id"]),
        nombre=str(r["nombre"]),
        celular_e164=str(r["celular"]),
        activo=bool(r["activo"]),
        registro=r["registro"],  # type: ignore[arg-type]
        ultima_consulta=r["ultima_consulta"],  # type: ignore[arg-type]
        casos_activos=int(r["casos_activos"]),  # type: ignore[call-overload]
        casos_ultimo_anio=int(r["casos_ultimo_anio"]),  # type: ignore[call-overload]
    )


async def _con_fincas(session: AsyncSession, filas: list[ProductorFila]) -> list[ProductorFila]:
    """Agrega a cada productor sus fincas (por orden de registro) con las especies de sus cultivos."""
    if not filas:
        return filas
    resultado = await session.execute(
        text(
            """
            SELECT f.id_productor, f.id_finca, f.nombre_finca, f.vereda_sector_finca,
                   COALESCE(array_agg(DISTINCT es.nombre_especie) FILTER (WHERE es.id_especie IS NOT NULL), '{}')
                       AS especies
            FROM fincas f
            LEFT JOIN cultivos cu ON cu.id_finca = f.id_finca
            LEFT JOIN cultivo_especie ce ON ce.id_cultivo = cu.id_cultivo
            LEFT JOIN especies es ON es.id_especie = ce.id_especie
            WHERE f.id_productor = ANY(:ids)
            GROUP BY f.id_finca
            ORDER BY f.id_finca
            """
        ),
        {"ids": [f.id for f in filas]},
    )
    por_productor: dict[str, list[FincaResumen]] = {}
    for r in resultado.mappings():
        especies = sorted(ESPECIES.a_codigo(n) for n in r["especies"])
        por_productor.setdefault(r["id_productor"], []).append(
            FincaResumen(r["id_finca"], r["nombre_finca"], normalizar_codigo(r["vereda_sector_finca"]), especies)
        )
    return [replace(f, fincas=por_productor.get(f.id, [])) for f in filas]


async def listar(
    session: AsyncSession, pagina: ParametrosPagina, filtros: FiltrosProductores
) -> tuple[list[ProductorFila], int]:
    """Filtros combinados (todos se suman con AND). Orden: nombre, sin importar tildes."""
    condiciones = ["TRUE"]
    parametros: dict[str, object] = {
        "estados_activos": ESTADOS_ACTIVOS,
        "limite": pagina.page_size,
        "desplazamiento": pagina.offset,
    }
    if filtros.q and normalizar_busqueda(filtros.q):
        busqueda = normalizar_busqueda(filtros.q)
        digitos = solo_digitos(filtros.q)
        por_nombre = f"{sin_tildes('nombre')} LIKE :q"
        parametros["q"] = f"%{busqueda}%"
        if len(digitos) >= 3:  # un número se busca en el celular, tal como lo teclea quien consulta
            condiciones.append(f"({por_nombre} OR celular LIKE :q_celular)")
            parametros["q_celular"] = f"%{digitos}%"
        else:
            condiciones.append(por_nombre)
    if filtros.estado:
        condiciones.append("activo" if filtros.estado == "activo" else "NOT activo")
    if filtros.vereda:
        parametros["veredas"] = await nombres_de_vereda(session, filtros.vereda)
        condiciones.append(
            "EXISTS (SELECT 1 FROM fincas f WHERE f.id_productor = base.id AND f.vereda_sector_finca = ANY(:veredas))"
        )
    if filtros.especie:
        parametros["especie"] = ESPECIES.a_nombre(filtros.especie)
        condiciones.append(
            "EXISTS (SELECT 1 FROM fincas f JOIN cultivos cu ON cu.id_finca = f.id_finca "
            "JOIN cultivo_especie ce ON ce.id_cultivo = cu.id_cultivo "
            "JOIN especies es ON es.id_especie = ce.id_especie "
            "WHERE f.id_productor = base.id AND es.nombre_especie = :especie)"
        )
    if filtros.con_casos == "con":
        condiciones.append("casos_activos > 0")
    elif filtros.con_casos == "sin":
        condiciones.append("casos_activos = 0")
    if filtros.finca and normalizar_busqueda(filtros.finca):
        parametros["finca"] = f"%{normalizar_busqueda(filtros.finca)}%"
        condiciones.append(
            f"EXISTS (SELECT 1 FROM fincas f WHERE f.id_productor = base.id "
            f"AND {sin_tildes('f.nombre_finca')} LIKE :finca)"
        )
    if filtros.desde:
        parametros["desde"] = filtros.desde
        condiciones.append("registro >= :desde")
    if filtros.hasta:
        parametros["hasta"] = filtros.hasta
        condiciones.append("registro <= :hasta")
    if filtros.consulta == "30":
        condiciones.append("ultima_consulta >= fn_ahora() - interval '30 days'")
    elif filtros.consulta == "90":  # quien nunca consultó también lleva más de 90 días sin consultar
        condiciones.append("(ultima_consulta IS NULL OR ultima_consulta < fn_ahora() - interval '90 days')")

    resultado = await session.execute(
        text(
            f"WITH {_BASE} SELECT {_COLUMNAS}, count(*) OVER () AS total FROM base "
            f"WHERE {' AND '.join(condiciones)} ORDER BY {sin_tildes('nombre')}, id "
            "LIMIT :limite OFFSET :desplazamiento"
        ),
        parametros,
    )
    filas = [dict(r) for r in resultado.mappings()]
    total = int(filas[0]["total"]) if filas else await _total(session, condiciones, parametros)
    return await _con_fincas(session, [_fila(r) for r in filas]), total


async def _total(session: AsyncSession, condiciones: list[str], parametros: dict[str, object]) -> int:
    """Una página fuera de rango no trae filas, pero igual se informa cuántos hay."""
    sin_pagina = {k: v for k, v in parametros.items() if k not in ("limite", "desplazamiento")}
    return int(
        await session.scalar(
            text(f"WITH {_BASE} SELECT count(*) FROM base WHERE {' AND '.join(condiciones)}"), sin_pagina
        )
        or 0
    )


async def obtener(session: AsyncSession, id_productor: str) -> ProductorFila | None:
    resultado = await session.execute(
        text(f"WITH {_BASE} SELECT {_COLUMNAS} FROM base WHERE id = :id"),
        {"id": id_productor, "estados_activos": ESTADOS_ACTIVOS},
    )
    fila = resultado.mappings().first()
    return (await _con_fincas(session, [_fila(dict(fila))]))[0] if fila else None


async def celular_registrado(session: AsyncSession, celular_e164: str, excluir_id: str | None = None) -> bool:
    return bool(
        await session.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM productores "
                "WHERE telefono_productor = :celular AND id_productor IS DISTINCT FROM :excluir)"
            ),
            {"celular": celular_e164, "excluir": excluir_id},
        )
    )


async def crear(session: AsyncSession, nombre: str, celular_e164: str, activo: bool) -> str:
    """Alta desde el panel: sin consentimiento todavía (el chatbot lo pide en el primer contacto, P-B08)."""
    hoy = await session.scalar(text("SELECT (fn_ahora() AT TIME ZONE 'America/Bogota')::date"))
    productor = Productor(
        nombre_productor=nombre,
        telefono_productor=celular_e164,
        estado_cuenta_productor=ACTIVO if activo else DESHABILITADO,
        fecha_registro_productor=hoy,
    )
    session.add(productor)
    await session.flush()
    return productor.id_productor


async def actualizar(session: AsyncSession, id_productor: str, nombre: str | None, celular_e164: str | None) -> None:
    await session.execute(
        text(
            "UPDATE productores SET nombre_productor = COALESCE(:nombre, nombre_productor), "
            "telefono_productor = COALESCE(:celular, telefono_productor) WHERE id_productor = :id"
        ),
        {"id": id_productor, "nombre": nombre, "celular": celular_e164},
    )


async def cambiar_estado(session: AsyncSession, id_productor: str, activo: bool) -> None:
    await session.execute(
        text("UPDATE productores SET estado_cuenta_productor = :estado WHERE id_productor = :id"),
        {"id": id_productor, "estado": ACTIVO if activo else DESHABILITADO},
    )


async def resumen(session: AsyncSession) -> tuple[int, int]:
    """(total, activos) para la cabecera de la pantalla de productores (B-05)."""
    fila = (
        await session.execute(
            text("SELECT count(*), count(*) FILTER (WHERE estado_cuenta_productor = 'ACTIVO') FROM productores")
        )
    ).one()
    return int(fila[0]), int(fila[1])
