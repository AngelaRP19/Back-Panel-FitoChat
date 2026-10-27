"""SQL de los casos del panel: bandeja, conteos, detalle y sus partes, sobre `vw_casos_detalle`.

El alcance se aplica siempre en SQL: el líder ve todos los casos y el técnico solo los de su asignación vigente.
Lo que queda fuera del alcance no se distingue de lo que no existe (`404`).
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.paginacion import ParametrosPagina
from app.core.catalogos_api import CATEGORIAS, ESPECIES, ESTADOS_CASO, EstadoCaso
from app.infrastructure.db.models import Asignacion
from app.modules.panel_api.cuentas.repository import normalizar_busqueda
from app.modules.panel_api.territorio_comun import nombres_de_vereda, sin_tildes

HOY_BOGOTA = "(fn_ahora() AT TIME ZONE 'America/Bogota')::date"
DIA_BOGOTA = "(v.fecha_envio_panel AT TIME ZONE 'America/Bogota')::date"
NO_IDENTIFICADA = "NO_IDENTIFICADA"


@dataclass(frozen=True)
class Alcance:
    """Quién consulta: el líder ve todos los casos; el técnico, los que tiene asignados."""

    id_usuario: str
    es_lider: bool


@dataclass(frozen=True)
class FiltrosCasos:
    q: str | None = None
    vereda: str | None = None
    finca_id: str | None = None
    cultivo_id: str | None = None
    especie: str | None = None
    categoria: str | None = None  # código de la API o NO_IDENTIFICADA
    tecnico_id: str | None = None  # id de la cuenta o `sin_asignar`
    coincidencia_min: int | None = None
    vencimiento: Literal["15", "30"] | None = None
    desde: date | None = None
    hasta: date | None = None


@dataclass(frozen=True)
class CasoFila:
    id_caso: str
    nombre_estado: str
    fecha_envio_panel: datetime | None
    vence_en: date | None
    fecha_cierre: datetime | None
    id_productor: str
    nombre_productor: str
    telefono_productor: str
    id_finca: str
    nombre_finca: str
    vereda_sector_finca: str
    latitud_finca: Decimal | None
    longitud_finca: Decimal | None
    id_cultivo: str
    nombre_cultivo: str
    tipo_cultivo: str
    nombre_especie: str
    descripcion_zona_afectada: str | None
    id_identificacion_preliminar: str | None
    resultado_analisis: str | None
    nombre_categoria_principal: str | None
    porcentaje_principal: Decimal | None
    nombre_alternativa: str | None
    porcentaje_alternativa: Decimal | None
    id_tecnico: str | None
    nombre_tecnico: str | None


_COLUMNAS_CASO = (
    "v.id_caso, v.nombre_estado, v.fecha_envio_panel, v.vence_en, v.fecha_cierre, "
    "v.id_productor, v.nombre_productor, v.telefono_productor, "
    "v.id_finca, v.nombre_finca, v.vereda_sector_finca, v.latitud_finca, v.longitud_finca, "
    "v.id_cultivo, v.nombre_cultivo, v.tipo_cultivo, v.nombre_especie, v.descripcion_zona_afectada, "
    "v.id_identificacion_preliminar, v.resultado_analisis, v.nombre_categoria_principal, v.porcentaje_principal, "
    "alt.nombre_categoria AS nombre_alternativa, alt.porcentaje_coincidencia AS porcentaje_alternativa, "
    "v.id_tecnico, v.nombre_tecnico"
)
# La segunda categoría con más coincidencia: la alternativa de un resultado ambiguo.
_DESDE_CASOS = """
    FROM vw_casos_detalle v
    LEFT JOIN LATERAL (
        SELECT cf.nombre_categoria, k.porcentaje_coincidencia
        FROM coincidencias_categoria k JOIN categorias_fitosanitarias cf ON cf.id_categoria = k.id_categoria
        WHERE k.id_identificacion = v.id_identificacion_preliminar
        ORDER BY k.porcentaje_coincidencia DESC, k.id_categoria
        OFFSET 1 LIMIT 1
    ) alt ON TRUE
"""


async def _condiciones(
    session: AsyncSession, alcance: Alcance, filtros: FiltrosCasos
) -> tuple[list[str], dict[str, Any]]:
    condiciones = ["TRUE"]
    parametros: dict[str, Any] = {}
    if not alcance.es_lider:
        condiciones.append("v.id_tecnico = :usuario")
        parametros["usuario"] = alcance.id_usuario
    texto = (filtros.q or "").strip().removeprefix("#")
    if normalizar_busqueda(texto):
        parametros["q"] = f"%{normalizar_busqueda(texto)}%"
        por_texto = f"{sin_tildes('v.nombre_productor || chr(32) || v.nombre_finca')} LIKE :q"
        if re.fullmatch(r"\d{1,8}", texto):  # un número también busca en el código del caso (#0241)
            parametros["numero"] = f"%{texto}%"
            por_texto += " OR lpad(ltrim(substr(v.id_caso, 5), '0'), 4, '0') LIKE :numero"
        condiciones.append(f"({por_texto})")
    if filtros.vereda:
        parametros["veredas"] = await nombres_de_vereda(session, filtros.vereda)
        condiciones.append("v.vereda_sector_finca = ANY(:veredas)")
    if filtros.finca_id:
        parametros["finca_id"] = filtros.finca_id
        condiciones.append("v.id_finca = :finca_id")
    if filtros.cultivo_id:
        parametros["cultivo_id"] = filtros.cultivo_id
        condiciones.append("v.id_cultivo = :cultivo_id")
    if filtros.especie:
        parametros["especie"] = ESPECIES.a_nombre(filtros.especie)
        condiciones.append("v.nombre_especie = :especie")
    if filtros.categoria == NO_IDENTIFICADA:
        condiciones.append("v.resultado_analisis = 'NO_IDENTIFICADO'")
    elif filtros.categoria:
        parametros["categoria"] = CATEGORIAS.a_nombre(filtros.categoria)
        condiciones.append("v.resultado_analisis <> 'NO_IDENTIFICADO' AND v.nombre_categoria_principal = :categoria")
    if filtros.tecnico_id == "sin_asignar":
        condiciones.append("v.id_tecnico IS NULL")
    elif filtros.tecnico_id:
        parametros["tecnico_id"] = filtros.tecnico_id
        condiciones.append("v.id_tecnico = :tecnico_id")
    if filtros.coincidencia_min:
        parametros["coincidencia_min"] = filtros.coincidencia_min
        condiciones.append("v.resultado_analisis <> 'NO_IDENTIFICADO' AND v.porcentaje_principal >= :coincidencia_min")
    if filtros.vencimiento:
        parametros["dias"] = int(filtros.vencimiento)
        condiciones.append(f"v.vence_en BETWEEN {HOY_BOGOTA} AND {HOY_BOGOTA} + :dias")
    if filtros.desde:
        parametros["desde"] = filtros.desde
        condiciones.append(f"{DIA_BOGOTA} >= :desde")
    if filtros.hasta:
        parametros["hasta"] = filtros.hasta
        condiciones.append(f"{DIA_BOGOTA} <= :hasta")
    return condiciones, parametros


async def listar(
    session: AsyncSession,
    alcance: Alcance,
    filtros: FiltrosCasos,
    estado: EstadoCaso | None,
    pagina: ParametrosPagina,
) -> tuple[list[CasoFila], int]:
    """Los más recientes (por envío al panel) primero; los que aún no se envían, al final (P-03)."""
    condiciones, parametros = await _condiciones(session, alcance, filtros)
    if estado is not None:
        parametros["estado"] = ESTADOS_CASO.a_nombre(estado)
        condiciones.append("v.nombre_estado = :estado")
    where = " AND ".join(condiciones)
    resultado = await session.execute(
        text(
            f"SELECT {_COLUMNAS_CASO}, count(*) OVER () AS total {_DESDE_CASOS} WHERE {where} "
            "ORDER BY v.fecha_envio_panel DESC NULLS LAST, v.id_caso DESC LIMIT :limite OFFSET :desplazamiento"
        ),
        {**parametros, "limite": pagina.page_size, "desplazamiento": pagina.offset},
    )
    filas = [dict(r) for r in resultado.mappings()]
    if filas:
        total = int(filas[0]["total"])
    else:  # página fuera de rango: sin filas, pero igual se informa el total
        total = int(
            await session.scalar(text(f"SELECT count(*) FROM vw_casos_detalle v WHERE {where}"), parametros) or 0
        )
    return [CasoFila(**{k: v for k, v in f.items() if k != "total"}) for f in filas], total


async def conteos(session: AsyncSession, alcance: Alcance, filtros: FiltrosCasos) -> dict[str, int]:
    """Casos por estado con el MISMO alcance y filtros del listado (sin el de estado): son las pestañas."""
    condiciones, parametros = await _condiciones(session, alcance, filtros)
    resultado = await session.execute(
        text(
            f"SELECT v.nombre_estado, count(*) FROM vw_casos_detalle v WHERE {' AND '.join(condiciones)} "
            "GROUP BY v.nombre_estado"
        ),
        parametros,
    )
    return {nombre: int(n) for nombre, n in resultado}


async def obtener(session: AsyncSession, alcance: Alcance, id_caso: str) -> CasoFila | None:
    condiciones = ["v.id_caso = :id_caso"]
    parametros: dict[str, Any] = {"id_caso": id_caso}
    if not alcance.es_lider:
        condiciones.append("v.id_tecnico = :usuario")
        parametros["usuario"] = alcance.id_usuario
    resultado = await session.execute(
        text(f"SELECT {_COLUMNAS_CASO} {_DESDE_CASOS} WHERE {' AND '.join(condiciones)}"), parametros
    )
    fila = resultado.mappings().first()
    return CasoFila(**dict(fila)) if fila else None


# --- Partes del detalle --------------------------------------------------------------------------------------------


async def casos_del_productor_ultimo_anio(session: AsyncSession, id_productor: str) -> int:
    return int(
        await session.scalar(
            text(
                "SELECT count(*) FROM vw_casos_detalle v WHERE v.id_productor = :p "
                "AND COALESCE(v.fecha_envio_panel, v.fecha_creacion_caso) >= fn_ahora() - interval '365 days'"
            ),
            {"p": id_productor},
        )
        or 0
    )


async def especies_del_cultivo(session: AsyncSession, id_cultivo: str) -> list[str]:
    resultado = await session.execute(
        text(
            "SELECT es.nombre_especie FROM cultivo_especie ce JOIN especies es ON es.id_especie = ce.id_especie "
            "WHERE ce.id_cultivo = :c ORDER BY es.nombre_especie"
        ),
        {"c": id_cultivo},
    )
    return [ESPECIES.a_codigo(n) for (n,) in resultado]


async def coincidencias(session: AsyncSession, id_identificacion: str) -> list[tuple[str, Decimal]]:
    """(nombre de la categoría, porcentaje), de la mayor a la menor coincidencia."""
    resultado = await session.execute(
        text(
            "SELECT cf.nombre_categoria, k.porcentaje_coincidencia FROM coincidencias_categoria k "
            "JOIN categorias_fitosanitarias cf ON cf.id_categoria = k.id_categoria "
            "WHERE k.id_identificacion = :i ORDER BY k.porcentaje_coincidencia DESC, k.id_categoria"
        ),
        {"i": id_identificacion},
    )
    return [(str(n), p) for n, p in resultado]


async def identificacion(session: AsyncSession, id_identificacion: str) -> dict[str, Any] | None:
    fila = (
        (
            await session.execute(
                text(
                    "SELECT tipo_resultado, explicacion_resultado, detalle_explicacion, fecha_analisis "
                    "FROM identificacion_preliminar WHERE id_identificacion = :i"
                ),
                {"i": id_identificacion},
            )
        )
        .mappings()
        .first()
    )
    return dict(fila) if fila else None


@dataclass(frozen=True)
class DiagnosticoFila:
    resultado_tecnico: str
    categoria: str | None
    motivo_visita: str | None
    descripcion: str | None
    observaciones: str | None
    fecha_diagnostico: datetime


async def diagnostico(session: AsyncSession, id_caso: str) -> DiagnosticoFila | None:
    """El diagnóstico solo existe cuando el técnico ya registró su resultado (la fila vacía no cuenta)."""
    fila = (
        (
            await session.execute(
                text(
                    "SELECT d.resultado_tecnico, cf.nombre_categoria AS categoria, d.motivo_visita, "
                    "d.descripcion_diagnostico AS descripcion, d.observaciones_diagnostico AS observaciones, "
                    "d.fecha_diagnostico FROM diagnosticos d "
                    "LEFT JOIN categorias_fitosanitarias cf ON cf.id_categoria = d.id_categoria_confirmada "
                    "WHERE d.id_caso = :c AND d.resultado_tecnico IS NOT NULL"
                ),
                {"c": id_caso},
            )
        )
        .mappings()
        .first()
    )
    return DiagnosticoFila(**dict(fila)) if fila else None


@dataclass(frozen=True)
class ResumenesCaso:
    fotos_por_tipo: dict[str, int] = field(default_factory=dict)
    respuestas: int = 0
    primera_respuesta: datetime | None = None
    ultima_respuesta: datetime | None = None
    ultimo_evento: tuple[str, str, datetime] | None = None  # (tipo, descripción, fecha)


async def resumenes(session: AsyncSession, id_caso: str) -> ResumenesCaso:
    fotos = await session.execute(
        text(
            "SELECT f.tipo_evidencia, count(*) FROM fotografias f "
            "JOIN respuestas r ON r.id_respuesta = f.id_respuesta "
            "JOIN diagnosticos d ON d.id_diagnostico = r.id_diagnostico "
            "WHERE d.id_caso = :c AND f.es_util = 'S' GROUP BY f.tipo_evidencia"
        ),
        {"c": id_caso},
    )
    cuestionario = (
        await session.execute(
            text(
                "SELECT count(*), min(r.fecha_respuesta), max(r.fecha_respuesta) FROM respuestas r "
                "JOIN diagnosticos d ON d.id_diagnostico = r.id_diagnostico WHERE d.id_caso = :c"
            ),
            {"c": id_caso},
        )
    ).one()
    evento = (
        await session.execute(
            text(
                "SELECT tipo_evento, descripcion_evento, fecha_evento FROM eventos WHERE id_caso = :c "
                "ORDER BY fecha_evento DESC, id_evento DESC LIMIT 1"
            ),
            {"c": id_caso},
        )
    ).first()
    return ResumenesCaso(
        fotos_por_tipo={str(t): int(n) for t, n in fotos},
        respuestas=int(cuestionario[0]),
        primera_respuesta=cuestionario[1],
        ultima_respuesta=cuestionario[2],
        ultimo_evento=(str(evento[0]), str(evento[1]), evento[2]) if evento else None,
    )


@dataclass(frozen=True)
class EvidenciaFila:
    id: str
    tipo: str
    numero: int
    util: bool
    clave: str
    recibida_en: datetime
    pregunta: str
    respuesta: str


async def evidencias(session: AsyncSession, id_caso: str) -> list[EvidenciaFila]:
    resultado = await session.execute(
        text(
            """
            SELECT f.id_fotografia, f.tipo_evidencia, f.es_util, f.ruta_fotografia, f.fecha_envio_fotografia,
                   p.texto_pregunta, r.texto_respuesta,
                   row_number() OVER (
                       PARTITION BY f.tipo_evidencia ORDER BY f.fecha_envio_fotografia, f.id_fotografia
                   ) AS numero
            FROM fotografias f
            JOIN respuestas r ON r.id_respuesta = f.id_respuesta
            JOIN preguntas p ON p.id_pregunta = r.id_pregunta
            JOIN diagnosticos d ON d.id_diagnostico = r.id_diagnostico
            WHERE d.id_caso = :c
            ORDER BY f.fecha_envio_fotografia, f.id_fotografia
            """
        ),
        {"c": id_caso},
    )
    return [
        EvidenciaFila(
            id=r["id_fotografia"],
            tipo=r["tipo_evidencia"],
            numero=int(r["numero"]),
            util=r["es_util"] == "S",
            clave=r["ruta_fotografia"],
            recibida_en=r["fecha_envio_fotografia"],
            pregunta=r["texto_pregunta"],
            respuesta=r["texto_respuesta"],
        )
        for r in resultado.mappings()
    ]


async def cuestionario(session: AsyncSession, id_caso: str) -> list[tuple[str, str, datetime]]:
    resultado = await session.execute(
        text(
            "SELECT p.texto_pregunta, r.texto_respuesta, r.fecha_respuesta FROM respuestas r "
            "JOIN preguntas p ON p.id_pregunta = r.id_pregunta "
            "JOIN diagnosticos d ON d.id_diagnostico = r.id_diagnostico WHERE d.id_caso = :c "
            "ORDER BY r.fecha_respuesta, r.id_respuesta"
        ),
        {"c": id_caso},
    )
    return [(str(p), str(r), f) for p, r, f in resultado]


@dataclass(frozen=True)
class EventoFila:
    tipo: str
    descripcion: str
    fecha: datetime
    actor_tipo: str | None
    actor_id: str | None
    actor_nombre: str | None


async def eventos(session: AsyncSession, id_caso: str) -> list[EventoFila]:
    resultado = await session.execute(
        text(
            """
            SELECT ev.tipo_evento, ev.descripcion_evento, ev.fecha_evento, ev.actor_tipo, ev.actor_id,
                   COALESCE(l.nombre_lider_tecnico, t.nombre_tecnico, p.nombre_productor) AS actor_nombre
            FROM eventos ev
            LEFT JOIN lideres_tecnicos l ON ev.actor_tipo = 'LIDER' AND l.id_lider_tecnico = ev.actor_id
            LEFT JOIN tecnicos t ON ev.actor_tipo = 'TECNICO' AND t.id_tecnico = ev.actor_id
            LEFT JOIN productores p ON ev.actor_tipo = 'PRODUCTOR' AND p.id_productor = ev.actor_id
            WHERE ev.id_caso = :c
            ORDER BY ev.fecha_evento, ev.id_evento
            """
        ),
        {"c": id_caso},
    )
    return [
        EventoFila(
            r["tipo_evento"],
            r["descripcion_evento"],
            r["fecha_evento"],
            r["actor_tipo"],
            r["actor_id"],
            r["actor_nombre"],
        )
        for r in resultado.mappings()
    ]


# --- Asignación ----------------------------------------------------------------------------------------------------


async def bloquear_caso(session: AsyncSession, id_caso: str) -> str | None:
    """Bloquea la fila del caso hasta el commit (dos asignaciones a la vez se turnan) y devuelve su estado.

    El candado va solo sobre `casos`, sin `JOIN`: si otra transacción cambia el estado mientras se espera, un `JOIN`
    con `estados` se reevalúa con la fila vieja de `estados` y la consulta dejaría de encontrar el caso.
    """
    bloqueado = await session.scalar(text("SELECT id_caso FROM casos WHERE id_caso = :c FOR UPDATE"), {"c": id_caso})
    if bloqueado is None:
        return None
    estado = await session.scalar(
        text("SELECT e.nombre_estado FROM casos c JOIN estados e ON e.id_estado = c.id_estado WHERE c.id_caso = :c"),
        {"c": id_caso},
    )
    return None if estado is None else str(estado)


async def tecnico_activo(session: AsyncSession, id_tecnico: str) -> str | None:
    """Nombre del técnico si existe en `tecnicos` y está activo; el líder no recibe casos (D-02)."""
    nombre = await session.scalar(
        text("SELECT nombre_tecnico FROM tecnicos WHERE id_tecnico = :t AND estado_cuenta_tecnico = 'ACTIVO'"),
        {"t": id_tecnico},
    )
    return None if nombre is None else str(nombre)


async def tecnico_vigente(session: AsyncSession, id_caso: str) -> tuple[str, str] | None:
    """(id, nombre) del técnico de la asignación vigente."""
    fila = (
        await session.execute(
            text(
                "SELECT a.id_tecnico, t.nombre_tecnico FROM asignaciones a "
                "JOIN tecnicos t ON t.id_tecnico = a.id_tecnico "
                "WHERE a.id_caso = :c AND a.fecha_fin IS NULL"
            ),
            {"c": id_caso},
        )
    ).first()
    return (str(fila[0]), str(fila[1])) if fila else None


async def reasignar(session: AsyncSession, id_caso: str, id_tecnico: str) -> None:
    """Cierra la asignación vigente (`fecha_fin`) y crea la nueva, con la misma hora del contexto."""
    ahora = await session.scalar(text("SELECT fn_ahora()"))
    await session.execute(
        text("UPDATE asignaciones SET fecha_fin = :ahora WHERE id_caso = :c AND fecha_fin IS NULL"),
        {"ahora": ahora, "c": id_caso},
    )
    session.add(Asignacion(id_caso=id_caso, id_tecnico=id_tecnico, fecha_asignacion=ahora))
    await session.flush()


async def pasar_a_estado(session: AsyncSession, id_caso: str, estado: EstadoCaso) -> None:
    """Cambia el estado; el trigger anota el evento con el actor del contexto de la transacción."""
    await session.execute(
        text("UPDATE casos SET id_estado = fn_id_estado(CAST(:nombre AS varchar)) WHERE id_caso = :c"),
        {"nombre": ESTADOS_CASO.a_nombre(estado), "c": id_caso},
    )


# --- Contadores de navegación -------------------------------------------------------------------------------------


async def navegacion(session: AsyncSession, alcance: Alcance) -> tuple[int, int]:
    """(pendientes, alertas generadas). El técnico ve solo lo suyo y las alertas no le corresponden."""
    if alcance.es_lider:
        pendientes = await session.scalar(
            text("SELECT count(*) FROM vw_casos_detalle WHERE nombre_estado = :e"),
            {"e": ESTADOS_CASO.a_nombre(EstadoCaso.PENDIENTE_REVISION)},
        )
        alertas = await session.scalar(text("SELECT count(*) FROM alertas WHERE estado_alerta = 'GENERADA'"))
        return int(pendientes or 0), int(alertas or 0)
    pendientes = await session.scalar(
        text("SELECT count(*) FROM vw_casos_detalle WHERE nombre_estado = :e AND id_tecnico = :t"),
        {"e": ESTADOS_CASO.a_nombre(EstadoCaso.EN_REVISION), "t": alcance.id_usuario},
    )
    return int(pendientes or 0), 0
