"""SQL de las alertas del panel: listado, detalle, destinatarios, parámetros y control de los envíos.

Las alertas las crea `sp_evaluar_alerta` (módulo 5); aquí solo se leen y se gestionan. Cada alerta conserva los
parámetros con que nació (D-08): los destinatarios se calculan con su `distancia_km`, no con la configuración actual.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.paginacion import ParametrosPagina
from app.core.catalogos_api import CATEGORIAS

ENVIADO = "ENVIADO"
SIMULADO = "SIMULADO"
FALLIDO = "FALLIDO"
PENDIENTE = "PENDIENTE"
ENVIANDO = "ENVIANDO"
ENTREGADOS = (ENVIADO, SIMULADO)
ALERTA = "ALERTA"
CORRECCION = "CORRECCION"
# Un envío «en curso» que lleva más de esto sin cerrarse se considera abandonado y otro proceso lo retoma.
MINUTOS_ENVIO_ABANDONADO = 10


@dataclass(frozen=True)
class AlertaFila:
    id: str
    estado: str
    id_categoria: str
    nombre_categoria: str
    generada_en: datetime
    comunicada_el: date | None
    num_minimo: int
    distancia_km: Decimal
    intervalo_dias: int
    recomendacion: str | None
    mensaje_enviado: str | None
    id_lider_gestion: str | None
    nombre_lider_gestion: str | None
    fecha_confirmacion: datetime | None
    justificacion_descarte: str | None
    mensaje_correccion: str | None
    fecha_correccion: datetime | None
    id_lider_correccion: str | None
    nombre_lider_correccion: str | None
    num_casos: int
    radio_km: float
    vereda: str | None
    notificados: int
    fallidos: int


_SELECT = """
    WITH fa AS (
        SELECT ac.id_alerta, f.vereda_sector_finca, f.ubicacion
        FROM alerta_casos ac
        JOIN casos c ON c.id_caso = ac.id_caso
        JOIN cultivos cu ON cu.id_cultivo = c.id_cultivo
        JOIN fincas f ON f.id_finca = cu.id_finca
    )
    SELECT a.id_alerta AS id, a.estado_alerta AS estado, a.id_categoria, cf.nombre_categoria,
           a.fecha_generacion_alerta AS generada_en, a.fecha_comunicacion_alerta AS comunicada_el,
           a.numero_minimo_casos AS num_minimo, a.distancia_km, a.intervalo_dias, a.recomendacion,
           a.mensaje_enviado, a.id_lider_gestion, lg.nombre_lider_tecnico AS nombre_lider_gestion,
           a.fecha_confirmacion, a.justificacion_descarte, a.mensaje_correccion, a.fecha_correccion,
           a.id_lider_correccion, lc.nombre_lider_tecnico AS nombre_lider_correccion,
           COALESCE(r.num_casos, 0) AS num_casos, COALESCE(r.radio_km, 0) AS radio_km, r.vereda,
           COALESCE(n.notificados, 0) AS notificados, COALESCE(n.fallidos, 0) AS fallidos
    FROM alertas a
    JOIN categorias_fitosanitarias cf ON cf.id_categoria = a.id_categoria
    LEFT JOIN lideres_tecnicos lg ON lg.id_lider_tecnico = a.id_lider_gestion
    LEFT JOIN lideres_tecnicos lc ON lc.id_lider_tecnico = a.id_lider_correccion
    LEFT JOIN LATERAL (
        SELECT count(*) AS num_casos,
               mode() WITHIN GROUP (ORDER BY x.vereda_sector_finca) AS vereda,
               (SELECT COALESCE(max(fn_distancia_km(p.ubicacion, q.ubicacion)), 0)
                  FROM fa p JOIN fa q ON q.id_alerta = p.id_alerta WHERE p.id_alerta = a.id_alerta) AS radio_km
        FROM fa x WHERE x.id_alerta = a.id_alerta
    ) r ON TRUE
    LEFT JOIN LATERAL (
        SELECT count(*) FILTER (WHERE d.estado_envio IN ('ENVIADO', 'SIMULADO')) AS notificados,
               count(*) FILTER (WHERE d.estado_envio = 'FALLIDO') AS fallidos
        FROM alerta_destinatarios d WHERE d.id_alerta = a.id_alerta AND d.tipo_envio = 'ALERTA'
    ) n ON TRUE
"""


def _fila(r: dict[str, object]) -> AlertaFila:
    return AlertaFila(**{**r, "radio_km": float(r["radio_km"]), "num_casos": int(r["num_casos"])})  # type: ignore[arg-type,call-overload]


async def listar(
    session: AsyncSession, pagina: ParametrosPagina, estado: str | None
) -> tuple[list[AlertaFila], int, dict[str, int]]:
    """Las más recientes primero, con el número de alertas por estado (las pestañas)."""
    condicion = "WHERE a.estado_alerta = :estado" if estado else ""
    parametros: dict[str, object] = {"limite": pagina.page_size, "desplazamiento": pagina.offset}
    if estado:
        parametros["estado"] = estado
    resultado = await session.execute(
        text(
            f"{_SELECT} {condicion} ORDER BY a.fecha_generacion_alerta DESC, a.id_alerta DESC "
            "LIMIT :limite OFFSET :desplazamiento"
        ),
        parametros,
    )
    filas = [_fila(dict(r)) for r in resultado.mappings()]
    conteos = {
        str(e): int(n) for e, n in await session.execute(text("SELECT estado_alerta, count(*) FROM alertas GROUP BY 1"))
    }
    total = conteos.get(estado, 0) if estado else sum(conteos.values())
    return filas, total, conteos


async def en_periodo(session: AsyncSession, desde: date, hasta: date) -> list[AlertaFila]:
    """Alertas generadas entre dos fechas de Bogotá (ambas incluidas), las más recientes primero (reportes)."""
    resultado = await session.execute(
        text(
            f"{_SELECT} WHERE (a.fecha_generacion_alerta AT TIME ZONE 'America/Bogota')::date "
            "BETWEEN :desde AND :hasta ORDER BY a.fecha_generacion_alerta DESC, a.id_alerta DESC"
        ),
        {"desde": desde, "hasta": hasta},
    )
    return [_fila(dict(r)) for r in resultado.mappings()]


async def obtener(session: AsyncSession, id_alerta: str) -> AlertaFila | None:
    resultado = await session.execute(text(f"{_SELECT} WHERE a.id_alerta = :a"), {"a": id_alerta})
    fila = resultado.mappings().first()
    return _fila(dict(fila)) if fila else None


@dataclass(frozen=True)
class CasoAlertaFila:
    id: str
    nombre_productor: str
    nombre_finca: str
    resultado_tecnico: str
    enviado_en: datetime | None
    latitud: Decimal | None
    longitud: Decimal | None
    id_tecnico: str | None


async def casos(session: AsyncSession, id_alerta: str) -> list[CasoAlertaFila]:
    resultado = await session.execute(
        text(
            """
            SELECT c.id_caso AS id, p.nombre_productor AS nombre_productor, f.nombre_finca AS nombre_finca,
                   COALESCE(d.resultado_tecnico, 'CONFIRMADO') AS resultado_tecnico,
                   c.fecha_envio_panel AS enviado_en, f.latitud_finca AS latitud, f.longitud_finca AS longitud,
                   a.id_tecnico
            FROM alerta_casos ac
            JOIN casos c ON c.id_caso = ac.id_caso
            JOIN cultivos cu ON cu.id_cultivo = c.id_cultivo
            JOIN fincas f ON f.id_finca = cu.id_finca
            JOIN productores p ON p.id_productor = f.id_productor
            LEFT JOIN diagnosticos d ON d.id_caso = c.id_caso
            LEFT JOIN asignaciones a ON a.id_caso = c.id_caso AND a.fecha_fin IS NULL
            WHERE ac.id_alerta = :a
            ORDER BY c.id_caso
            """
        ),
        {"a": id_alerta},
    )
    return [CasoAlertaFila(**dict(r)) for r in resultado.mappings()]


# --- Destinatarios -------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DestinoFila:
    id_productor: str
    nombre: str
    telefono: str
    activo: bool
    vereda: str  # nombre guardado
    zona_alerta: bool


async def destinatarios(session: AsyncSession, id_alerta: str, km: Decimal) -> list[DestinoFila]:
    """Productores con alguna finca a `km` o menos de las fincas de los casos, con la vereda de esa finca."""
    resultado = await session.execute(
        text(
            """
            WITH origen AS (
                SELECT DISTINCT f.id_finca, f.ubicacion, f.vereda_sector_finca
                FROM alerta_casos ac
                JOIN casos c ON c.id_caso = ac.id_caso
                JOIN cultivos cu ON cu.id_cultivo = c.id_cultivo
                JOIN fincas f ON f.id_finca = cu.id_finca
                WHERE ac.id_alerta = :a
            )
            SELECT p.id_productor, p.nombre_productor AS nombre, p.telefono_productor AS telefono,
                   p.estado_cuenta_productor = 'ACTIVO' AS activo, f2.vereda_sector_finca AS vereda,
                   bool_or(f2.vereda_sector_finca IN (SELECT vereda_sector_finca FROM origen)) AS zona_alerta
            FROM fincas f2
            JOIN productores p ON p.id_productor = f2.id_productor
            WHERE EXISTS (SELECT 1 FROM origen o WHERE fn_distancia_km(o.ubicacion, f2.ubicacion) <= :km)
            GROUP BY p.id_productor, p.nombre_productor, p.telefono_productor, p.estado_cuenta_productor,
                     f2.vereda_sector_finca
            ORDER BY f2.vereda_sector_finca, p.nombre_productor, p.id_productor
            """
        ),
        {"a": id_alerta, "km": float(km)},
    )
    return [DestinoFila(**dict(r)) for r in resultado.mappings()]


# --- Decisiones del líder ------------------------------------------------------------------------------------------


async def bloquear(session: AsyncSession, id_alerta: str) -> str | None:
    """Bloquea la alerta hasta el commit (dos decisiones a la vez se turnan) y devuelve su estado."""
    estado = await session.scalar(
        text("SELECT estado_alerta FROM alertas WHERE id_alerta = :a FOR UPDATE"), {"a": id_alerta}
    )
    return None if estado is None else str(estado)


async def decidir(
    session: AsyncSession, id_alerta: str, estado: str, id_lider: str, justificacion: str | None = None
) -> None:
    """Confirma o descarta: guarda quién decidió y cuándo (`fecha_confirmacion` es la fecha de la decisión)."""
    await session.execute(
        text(
            "UPDATE alertas SET estado_alerta = :estado, id_lider_gestion = :lider, fecha_confirmacion = fn_ahora(), "
            "justificacion_descarte = :justificacion WHERE id_alerta = :a"
        ),
        {"estado": estado, "lider": id_lider, "justificacion": justificacion, "a": id_alerta},
    )


async def guardar_borrador(session: AsyncSession, id_alerta: str, mensaje: str) -> None:
    await session.execute(
        text("UPDATE alertas SET recomendacion = :m WHERE id_alerta = :a"), {"m": mensaje, "a": id_alerta}
    )


async def fijar_mensaje(session: AsyncSession, id_alerta: str, recomendacion: str, mensaje_enviado: str) -> None:
    await session.execute(
        text("UPDATE alertas SET recomendacion = :r, mensaje_enviado = :m WHERE id_alerta = :a"),
        {"r": recomendacion, "m": mensaje_enviado, "a": id_alerta},
    )


async def fijar_correccion(session: AsyncSession, id_alerta: str, mensaje: str, id_lider: str) -> None:
    await session.execute(
        text("UPDATE alertas SET mensaje_correccion = :m, id_lider_correccion = :l WHERE id_alerta = :a"),
        {"m": mensaje, "l": id_lider, "a": id_alerta},
    )


async def marcar_comunicada(session: AsyncSession, id_alerta: str) -> None:
    await session.execute(
        text(
            "UPDATE alertas SET estado_alerta = 'COMUNICADA', "
            "fecha_comunicacion_alerta = (fn_ahora() AT TIME ZONE 'America/Bogota')::date WHERE id_alerta = :a"
        ),
        {"a": id_alerta},
    )


async def marcar_corregida(session: AsyncSession, id_alerta: str) -> None:
    await session.execute(
        text("UPDATE alertas SET estado_alerta = 'CORREGIDA', fecha_correccion = fn_ahora() WHERE id_alerta = :a"),
        {"a": id_alerta},
    )


# --- Control de los envíos (reanudable y sin duplicados) -----------------------------------------------------------


async def asegurar_envios(session: AsyncSession, id_alerta: str, tipo: str, ids_productor: list[str]) -> None:
    """Una fila por (alerta, productor, tipo). Las que ya existen (enviadas o no) no se tocan."""
    if not ids_productor:
        return
    await session.execute(
        text(
            "INSERT INTO alerta_destinatarios "
            "(id_alerta_destinatario, id_alerta, id_productor, tipo_envio, estado_envio) "
            "SELECT fn_generar_id('seq_alerta_destinatarios', 'ADE', '-', 12), :a, p, :t, 'PENDIENTE' "
            "FROM unnest(CAST(:ids AS varchar[])) AS p ON CONFLICT (id_alerta, id_productor, tipo_envio) DO NOTHING"
        ),
        {"a": id_alerta, "t": tipo, "ids": ids_productor},
    )


async def reintentar_fallidos(session: AsyncSession, id_alerta: str, tipo: str) -> int:
    resultado = await session.execute(
        text(
            "UPDATE alerta_destinatarios SET estado_envio = 'PENDIENTE', detalle_error = NULL "
            "WHERE id_alerta = :a AND tipo_envio = :t AND estado_envio = 'FALLIDO'"
        ),
        {"a": id_alerta, "t": tipo},
    )
    return int(resultado.rowcount)  # type: ignore[attr-defined]


@dataclass(frozen=True)
class EnvioReclamado:
    id: str
    id_productor: str
    telefono: str


async def reclamar_envios(session: AsyncSession, id_alerta: str, tipo: str, limite: int) -> list[EnvioReclamado]:
    """Toma hasta `limite` envíos pendientes y los marca «en curso»; dos procesos nunca reciben el mismo."""
    resultado = await session.execute(
        text(
            """
            UPDATE alerta_destinatarios d SET estado_envio = 'ENVIANDO', fecha_envio = fn_ahora()
            WHERE d.id_alerta_destinatario IN (
                SELECT id_alerta_destinatario FROM alerta_destinatarios
                WHERE id_alerta = :a AND tipo_envio = :t
                  AND (estado_envio = 'PENDIENTE'
                       OR (estado_envio = 'ENVIANDO' AND fecha_envio < fn_ahora() - make_interval(mins => :minutos)))
                ORDER BY id_alerta_destinatario
                LIMIT :n FOR UPDATE SKIP LOCKED
            )
            RETURNING d.id_alerta_destinatario AS id, d.id_productor,
                      (SELECT telefono_productor FROM productores WHERE id_productor = d.id_productor) AS telefono
            """
        ),
        {"a": id_alerta, "t": tipo, "n": limite, "minutos": MINUTOS_ENVIO_ABANDONADO},
    )
    return [EnvioReclamado(**dict(r)) for r in resultado.mappings()]


async def liberar_envios(session: AsyncSession, ids: list[str]) -> None:
    """Devuelve a pendientes los envíos reclamados que no llegaron a intentarse."""
    if ids:
        await session.execute(
            text(
                "UPDATE alerta_destinatarios SET estado_envio = 'PENDIENTE', fecha_envio = NULL "
                "WHERE id_alerta_destinatario = ANY(:ids) AND estado_envio = 'ENVIANDO'"
            ),
            {"ids": ids},
        )


async def registrar_resultado(session: AsyncSession, id_envio: str, estado: str, error: str | None) -> None:
    await session.execute(
        text(
            "UPDATE alerta_destinatarios SET estado_envio = :e, detalle_error = :err, fecha_envio = fn_ahora() "
            "WHERE id_alerta_destinatario = :i"
        ),
        {"e": estado, "err": error, "i": id_envio},
    )


async def envios_sin_cerrar(session: AsyncSession, id_alerta: str, tipo: str) -> int:
    """Envíos que todavía no se intentaron (pendientes) o que otro proceso tiene en curso."""
    return int(
        await session.scalar(
            text(
                "SELECT count(*) FROM alerta_destinatarios WHERE id_alerta = :a AND tipo_envio = :t "
                "AND estado_envio IN ('PENDIENTE', 'ENVIANDO')"
            ),
            {"a": id_alerta, "t": tipo},
        )
        or 0
    )


async def productores_que_recibieron(session: AsyncSession, id_alerta: str) -> list[str]:
    """Quienes recibieron la alerta original: a ellos, y solo a ellos, va la corrección."""
    resultado = await session.execute(
        text(
            "SELECT id_productor FROM alerta_destinatarios WHERE id_alerta = :a AND tipo_envio = 'ALERTA' "
            "AND estado_envio IN ('ENVIADO', 'SIMULADO') ORDER BY id_productor"
        ),
        {"a": id_alerta},
    )
    return [str(p) for (p,) in resultado]


async def contar_envios(session: AsyncSession, id_alerta: str, tipo: str) -> dict[str, int]:
    resultado = await session.execute(
        text(
            "SELECT estado_envio, count(*) FROM alerta_destinatarios WHERE id_alerta = :a AND tipo_envio = :t "
            "GROUP BY 1"
        ),
        {"a": id_alerta, "t": tipo},
    )
    return {str(e): int(n) for e, n in resultado}


# --- Parámetros epidemiológicos ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ParametroFila:
    id_categoria: str
    nombre_categoria: str
    transmisible: bool
    num_casos: int | None
    distancia_km: Decimal | None
    intervalo_dias: int | None
    actualizado_en: datetime | None


async def parametros(session: AsyncSession) -> list[ParametroFila]:
    resultado = await session.execute(
        text(
            "SELECT cf.id_categoria, cf.nombre_categoria, cf.es_transmisible = 'S' AS transmisible, "
            "p.numero_minimo_casos AS num_casos, p.distancia_km, p.intervalo_dias, "
            "p.fecha_actualizacion AS actualizado_en "
            "FROM categorias_fitosanitarias cf "
            "LEFT JOIN parametros_epidemiologicos p ON p.id_categoria = cf.id_categoria "
            "ORDER BY cf.id_categoria"
        )
    )
    return [ParametroFila(**dict(r)) for r in resultado.mappings()]


async def ultimas_ediciones(session: AsyncSession) -> dict[str, tuple[str, str]]:
    """`{código de categoría: (id del líder, nombre)}` de la última edición de parámetros, según la auditoría."""
    resultado = await session.execute(
        text(
            "SELECT a.descripcion, a.id_lider_tecnico, l.nombre_lider_tecnico FROM auditoria a "
            "LEFT JOIN lideres_tecnicos l ON l.id_lider_tecnico = a.id_lider_tecnico "
            "WHERE a.accion = 'EDITAR_PARAMETROS' ORDER BY a.fecha_auditoria DESC, a.id_auditoria DESC"
        )
    )
    ultimas: dict[str, tuple[str, str]] = {}
    for descripcion, id_lider, nombre in resultado:
        codigo = str(descripcion).rsplit(" ", 1)[-1]
        if id_lider and codigo not in ultimas:
            ultimas[codigo] = (str(id_lider), str(nombre))
    return ultimas


async def guardar_parametros(
    session: AsyncSession, id_categoria: str, num_casos: int, distancia_km: float, intervalo_dias: int
) -> None:
    await session.execute(
        text(
            "INSERT INTO parametros_epidemiologicos "
            "(id_parametro, id_categoria, numero_minimo_casos, distancia_km, intervalo_dias, fecha_actualizacion) "
            "VALUES (fn_generar_id('seq_parametros_epidemiologicos', 'PAR', '-', 10), :c, :n, :km, :d, fn_ahora()) "
            "ON CONFLICT (id_categoria) DO UPDATE SET numero_minimo_casos = EXCLUDED.numero_minimo_casos, "
            "distancia_km = EXCLUDED.distancia_km, intervalo_dias = EXCLUDED.intervalo_dias, "
            "fecha_actualizacion = EXCLUDED.fecha_actualizacion"
        ),
        {"c": id_categoria, "n": num_casos, "km": round(distancia_km, 2), "d": intervalo_dias},
    )


async def id_categoria(session: AsyncSession, codigo: str) -> str | None:
    valor = await session.scalar(
        text("SELECT id_categoria FROM categorias_fitosanitarias WHERE nombre_categoria = :n"),
        {"n": CATEGORIAS.a_nombre(codigo)},
    )
    return None if valor is None else str(valor)


# --- «Seguir» (P-06): sin tabla propia, el estado sale de la auditoría ----------------------------------------------


async def esta_siguiendo(session: AsyncSession, id_alerta: str, id_tecnico: str) -> bool:
    accion = await session.scalar(
        text(
            "SELECT accion FROM auditoria WHERE id_tecnico = :t AND accion IN ('SEGUIR_ALERTA', 'DEJAR_SEGUIR') "
            "AND descripcion LIKE :d ORDER BY fecha_auditoria DESC, id_auditoria DESC LIMIT 1"
        ),
        {"t": id_tecnico, "d": f"%{id_alerta}%"},
    )
    return bool(accion == "SEGUIR_ALERTA")
