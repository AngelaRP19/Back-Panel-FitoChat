"""Consultas ligeras del inicio del panel: pocos recorridos agregados, sin listas largas."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.catalogos_api import CATEGORIAS, ESTADOS_CASO, EstadoCaso

HORAS_ESPERA = 48
DIAS_AVISO_VENCIMIENTO = 15
LIMITE_ANTIGUOS = 5
LIMITE_ALERTAS_RECIENTES = 4
LIMITE_DIAGNOSTICOS_RECIENTES = 3

HOY = "(fn_ahora() AT TIME ZONE 'America/Bogota')::date"
INICIO_MES = "(date_trunc('month', fn_ahora() AT TIME ZONE 'America/Bogota') AT TIME ZONE 'America/Bogota')"

# Todos los casos (líder) o solo los del técnico (su asignación vigente), con su diagnóstico si lo hay.
_BASE = """
    base AS (
        SELECT c.id_caso, e.nombre_estado, c.fecha_envio_panel, c.fecha_cierre, c.vence_en, a.id_tecnico,
               d.resultado_tecnico, d.id_categoria_confirmada, d.fecha_diagnostico
        FROM casos c
        JOIN estados e ON e.id_estado = c.id_estado
        LEFT JOIN asignaciones a ON a.id_caso = c.id_caso AND a.fecha_fin IS NULL
        LEFT JOIN diagnosticos d ON d.id_caso = c.id_caso
        WHERE (CAST(:tecnico AS varchar) IS NULL OR a.id_tecnico = CAST(:tecnico AS varchar))
    )
"""


@dataclass(frozen=True)
class Kpis:
    pendientes: int
    pendientes_mas_48h: int
    en_revision: int
    en_revision_mas_48h: int
    cerrados: int
    confirmados: int
    otra_patologia: int
    no_identificado: int
    requiere_visita: int
    proximos_a_vencer: int


async def kpis(session: AsyncSession, id_tecnico: str | None) -> Kpis:
    nombre = {e: ESTADOS_CASO.a_nombre(e) for e in EstadoCaso}
    mes = f"fecha_cierre >= {INICIO_MES} AND fecha_cierre < {INICIO_MES} + interval '1 month'"
    fila = (
        await session.execute(
            text(
                f"""
                WITH {_BASE}
                SELECT
                    count(*) FILTER (WHERE nombre_estado = :pendiente) AS pendientes,
                    count(*) FILTER (WHERE nombre_estado = :pendiente
                                     AND fecha_envio_panel < fn_ahora() - make_interval(hours => :horas)) AS pend48,
                    count(*) FILTER (WHERE nombre_estado = :en_revision) AS en_revision,
                    count(*) FILTER (WHERE nombre_estado = :en_revision
                                     AND fecha_envio_panel < fn_ahora() - make_interval(hours => :horas)) AS rev48,
                    count(*) FILTER (WHERE nombre_estado = :cerrado AND {mes}) AS cerrados,
                    count(*) FILTER (WHERE nombre_estado = :cerrado AND {mes} AND resultado_tecnico = 'CONFIRMADO')
                        AS confirmados,
                    count(*) FILTER (WHERE nombre_estado = :cerrado AND {mes} AND resultado_tecnico = 'OTRA_PATOLOGIA')
                        AS otra,
                    count(*) FILTER (WHERE nombre_estado = :cerrado AND {mes} AND resultado_tecnico = 'NO_IDENTIFICADO')
                        AS noid,
                    count(*) FILTER (WHERE nombre_estado = :cerrado AND {mes} AND resultado_tecnico = 'REQUIERE_VISITA')
                        AS visita,
                    count(*) FILTER (WHERE nombre_estado = :cerrado
                                     AND vence_en BETWEEN {HOY} AND {HOY} + :dias) AS proximos
                FROM base
                """
            ),
            {
                "tecnico": id_tecnico,
                "pendiente": nombre[EstadoCaso.PENDIENTE_REVISION],
                "en_revision": nombre[EstadoCaso.EN_REVISION],
                "cerrado": nombre[EstadoCaso.CERRADO],
                "horas": HORAS_ESPERA,
                "dias": DIAS_AVISO_VENCIMIENTO,
            },
        )
    ).one()
    return Kpis(*(int(v) for v in fila))


@dataclass(frozen=True)
class PendienteFila:
    id: str
    nombre_productor: str
    nombre_estado: str
    enviado: datetime | None
    id_tecnico: str | None


async def sin_diagnostico_antiguos(session: AsyncSession) -> list[PendienteFila]:
    """Los casos Pendientes o En revisión que más llevan esperando desde que llegaron al panel."""
    resultado = await session.execute(
        text(
            """
            SELECT v.id_caso AS id, v.nombre_productor, v.nombre_estado, v.fecha_envio_panel AS enviado, v.id_tecnico
            FROM vw_casos_detalle v
            WHERE v.nombre_estado IN (:pendiente, :en_revision) AND v.fecha_envio_panel IS NOT NULL
            ORDER BY v.fecha_envio_panel, v.id_caso
            LIMIT :limite
            """
        ),
        {
            "pendiente": ESTADOS_CASO.a_nombre(EstadoCaso.PENDIENTE_REVISION),
            "en_revision": ESTADOS_CASO.a_nombre(EstadoCaso.EN_REVISION),
            "limite": LIMITE_ANTIGUOS,
        },
    )
    return [PendienteFila(**dict(r)) for r in resultado.mappings()]


async def por_atender(session: AsyncSession, id_tecnico: str) -> list[PendienteFila]:
    """De un técnico: En revisión (los que más esperan primero) y luego Registrados sin cerrar."""
    resultado = await session.execute(
        text(
            """
            SELECT v.id_caso AS id, v.nombre_productor, v.nombre_estado, v.fecha_envio_panel AS enviado, v.id_tecnico
            FROM vw_casos_detalle v
            WHERE v.id_tecnico = :t AND v.nombre_estado IN (:en_revision, :registrado)
            ORDER BY (v.nombre_estado = :en_revision) DESC, v.fecha_envio_panel NULLS LAST, v.id_caso
            """
        ),
        {
            "t": id_tecnico,
            "en_revision": ESTADOS_CASO.a_nombre(EstadoCaso.EN_REVISION),
            "registrado": ESTADOS_CASO.a_nombre(EstadoCaso.REGISTRADO),
        },
    )
    return [PendienteFila(**dict(r)) for r in resultado.mappings()]


@dataclass(frozen=True)
class DiagnosticoFila:
    id_caso: str
    resultado: str
    categoria: str | None
    fecha: datetime


async def diagnosticos_recientes(session: AsyncSession, id_tecnico: str) -> list[DiagnosticoFila]:
    resultado = await session.execute(
        text(
            """
            SELECT d.id_caso, d.resultado_tecnico AS resultado, cf.nombre_categoria AS categoria,
                   d.fecha_diagnostico AS fecha
            FROM diagnosticos d
            JOIN asignaciones a ON a.id_caso = d.id_caso AND a.fecha_fin IS NULL
            LEFT JOIN categorias_fitosanitarias cf ON cf.id_categoria = d.id_categoria_confirmada
            WHERE a.id_tecnico = :t AND d.resultado_tecnico IS NOT NULL
            ORDER BY d.fecha_diagnostico DESC, d.id_caso DESC
            LIMIT :limite
            """
        ),
        {"t": id_tecnico, "limite": LIMITE_DIAGNOSTICOS_RECIENTES},
    )
    return [DiagnosticoFila(**dict(r)) for r in resultado.mappings()]


async def nombre_de_lider(session: AsyncSession) -> str | None:
    nombre = await session.scalar(
        text(
            "SELECT nombre_lider_tecnico FROM lideres_tecnicos WHERE estado_cuenta_lider_tecnico = 'ACTIVO' "
            "ORDER BY id_lider_tecnico LIMIT 1"
        )
    )
    return None if nombre is None else str(nombre)


async def nombre_de_tecnico(session: AsyncSession, id_tecnico: str) -> str:
    return str(
        await session.scalar(text("SELECT nombre_tecnico FROM tecnicos WHERE id_tecnico = :t"), {"t": id_tecnico}) or ""
    )


@dataclass(frozen=True)
class CargaFila:
    id: str
    nombre: str
    activos: int
    en_revision: int


async def carga_de_tecnicos(session: AsyncSession) -> list[CargaFila]:
    """Técnicos activos con su carga. El líder no recibe casos con su rol (D-02): no aparece."""
    resultado = await session.execute(
        text(
            """
            SELECT t.id_tecnico AS id, t.nombre_tecnico AS nombre,
                   count(c.id_caso) FILTER (WHERE e.nombre_estado IN (:en_revision, :registrado)) AS activos,
                   count(c.id_caso) FILTER (WHERE e.nombre_estado = :en_revision) AS en_revision
            FROM tecnicos t
            LEFT JOIN asignaciones a ON a.id_tecnico = t.id_tecnico AND a.fecha_fin IS NULL
            LEFT JOIN casos c ON c.id_caso = a.id_caso
            LEFT JOIN estados e ON e.id_estado = c.id_estado
            WHERE t.estado_cuenta_tecnico = 'ACTIVO'
            GROUP BY t.id_tecnico, t.nombre_tecnico
            ORDER BY activos DESC, t.nombre_tecnico, t.id_tecnico
            """
        ),
        {
            "en_revision": ESTADOS_CASO.a_nombre(EstadoCaso.EN_REVISION),
            "registrado": ESTADOS_CASO.a_nombre(EstadoCaso.REGISTRADO),
        },
    )
    return [CargaFila(r["id"], r["nombre"], int(r["activos"]), int(r["en_revision"])) for r in resultado.mappings()]


async def casos_por_categoria(session: AsyncSession, periodo: str) -> dict[str, int]:
    """Diagnósticos confirmados (`CONFIRMADO` y `OTRA_PATOLOGIA`) por categoría dentro del periodo."""
    ventana = {
        "30d": "fn_ahora() - interval '30 days'",
        "90d": "fn_ahora() - interval '90 days'",
        "anio": "(date_trunc('year', fn_ahora() AT TIME ZONE 'America/Bogota') AT TIME ZONE 'America/Bogota')",
    }[periodo]
    resultado = await session.execute(
        text(
            f"""
            SELECT cf.nombre_categoria, count(*) FROM diagnosticos d
            JOIN categorias_fitosanitarias cf ON cf.id_categoria = d.id_categoria_confirmada
            WHERE d.resultado_tecnico IN ('CONFIRMADO', 'OTRA_PATOLOGIA')
              AND d.fecha_diagnostico >= {ventana} AND d.fecha_diagnostico <= fn_ahora()
            GROUP BY cf.nombre_categoria
            """
        )
    )
    return {CATEGORIAS.a_codigo(str(n)): int(c) for n, c in resultado}
