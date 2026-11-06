"""Datos semilla v2 (guía §6.5). Idempotente: se puede correr varias veces sin duplicar ni gastar ids.

Uso:  python -m app.infrastructure.db.seed     (requiere la base y la migración aplicada)

Los nombres salen de `core/catalogos_api.py`, así que lo sembrado y los códigos de la API no pueden separarse.
Cada INSERT usa `SELECT ... WHERE NOT EXISTS` (para no consumir la secuencia si la fila ya existe) más
`ON CONFLICT DO NOTHING` (por si dos procesos siembran a la vez).

No se siembran las preguntas del cuestionario: su texto no está en los documentos del proyecto (P-B10).
"""

import logging
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Connection, create_engine, text

from app.core import catalogos_api as api
from app.core.config import get_database_settings
from app.core.logging import configure_logging
from app.infrastructure.db.ids import PREFIJOS_ID, nombre_secuencia

logger = logging.getLogger(__name__)

DESCRIPCIONES_ESTADO: dict[api.EstadoCaso, str] = {
    api.EstadoCaso.CREADO: "El productor inició el caso por WhatsApp.",
    api.EstadoCaso.EN_ANALISIS: "Se procesan las fotos y el cuestionario del productor.",
    api.EstadoCaso.PENDIENTE_REVISION: "El caso se envió al panel y aún no tiene técnico asignado.",
    api.EstadoCaso.EN_REVISION: "Hay un técnico asignado y todavía no registra el diagnóstico.",
    api.EstadoCaso.REGISTRADO: "El técnico asignado guardó su diagnóstico.",
    api.EstadoCaso.CERRADO: "El caso se cerró; corre el plazo de conservación de los datos.",
    api.EstadoCaso.DESCARTADO: "El caso se descartó por inactividad de 72 horas o porque el productor salió.",
}

# Solo Trips es transmisible (decisión del equipo); magnesio y las demás quedan fuera de la epidemiología.
CATEGORIAS_TRANSMISIBLES = frozenset({api.CategoriaFito.TRIPS})

ESTADOS: list[tuple[str, str]] = [(api.ESTADOS_CASO.a_nombre(e), DESCRIPCIONES_ESTADO[e]) for e in api.EstadoCaso]
CATEGORIAS: list[tuple[str, str]] = [
    (api.CATEGORIAS.a_nombre(c), "S" if c in CATEGORIAS_TRANSMISIBLES else "N") for c in api.CategoriaFito
]
ESPECIES: list[str] = [api.ESPECIES.a_nombre(e) for e in api.EspecieCultivo]


@dataclass(frozen=True)
class ParametroSemilla:
    categoria: api.CategoriaFito
    numero_minimo_casos: int
    distancia_km: Decimal
    intervalo_dias: int


# Trips: 3,5 km y 30 días son decisión del equipo. El número mínimo de casos NO está definido (P-B02):
# 3 es un valor PROVISIONAL; el líder lo edita desde el panel.
PARAMETROS: list[ParametroSemilla] = [
    ParametroSemilla(api.CategoriaFito.TRIPS, numero_minimo_casos=3, distancia_km=Decimal("3.50"), intervalo_dias=30),
]
MINIMO_DE_CASOS_PROVISIONAL = True

# Nombres de la semilla genérica anterior (no son los del proyecto).
SEMILLA_ANTERIOR: dict[str, frozenset[str]] = {
    "estados": frozenset({"Abierto", "Diagnosticado"}),
    "categorias_fitosanitarias": frozenset(
        {"Enfermedad fúngica", "Enfermedad bacteriana", "Enfermedad viral", "Plaga", "Deficiencia nutricional"}
    ),
    "especies": frozenset({"Curuba"}),
}
_COLUMNA_NOMBRE = {
    "estados": "nombre_estado",
    "categorias_fitosanitarias": "nombre_categoria",
    "especies": "nombre_especie",
}


class SemillaAnteriorError(RuntimeError):
    """La base tiene la semilla genérica anterior; hay que recrearla en vez de mezclar datos."""


def detectar_semilla_anterior(conn: Connection) -> dict[str, list[str]]:
    encontrados: dict[str, list[str]] = {}
    for tabla, nombres in SEMILLA_ANTERIOR.items():
        columna = _COLUMNA_NOMBRE[tabla]
        filas = conn.execute(
            text(f"SELECT {columna} FROM {tabla} WHERE {columna} = ANY(:nombres)"), {"nombres": list(nombres)}
        )
        presentes = sorted(fila[0] for fila in filas)
        if presentes:
            encontrados[tabla] = presentes
    return encontrados


def _id_sql(tabla: str) -> str:
    spec = PREFIJOS_ID[tabla]
    return f"fn_generar_id('{nombre_secuencia(tabla)}', '{spec.prefijo}', '{spec.separador}', {spec.largo})"


def _insertar_por_nombre(
    conn: Connection, tabla: str, id_col: str, nombre_col: str, filas: list[dict[str, str]]
) -> int:
    columnas = list(filas[0])
    valores = ", ".join(f"CAST(:{c} AS varchar)" for c in columnas)
    sql = text(
        f"INSERT INTO {tabla} ({id_col}, {', '.join(columnas)}) "
        f"SELECT {_id_sql(tabla)}, {valores} "
        f"WHERE NOT EXISTS (SELECT 1 FROM {tabla} WHERE {nombre_col} = :{nombre_col}) "
        "ON CONFLICT DO NOTHING"
    )
    return sum(conn.execute(sql, fila).rowcount for fila in filas)


def _insertar_parametro(conn: Connection, parametro: ParametroSemilla) -> int:
    return conn.execute(
        text(
            "INSERT INTO parametros_epidemiologicos "
            "(id_parametro, id_categoria, numero_minimo_casos, distancia_km, intervalo_dias, fecha_actualizacion) "
            f"SELECT {_id_sql('parametros_epidemiologicos')}, c.id_categoria, "
            "CAST(:numero_minimo_casos AS integer), CAST(:distancia_km AS numeric), "
            "CAST(:intervalo_dias AS integer), now() "
            "FROM categorias_fitosanitarias c "
            "WHERE c.nombre_categoria = :categoria AND NOT EXISTS "
            "(SELECT 1 FROM parametros_epidemiologicos p WHERE p.id_categoria = c.id_categoria) "
            "ON CONFLICT DO NOTHING"
        ),
        {
            "categoria": api.CATEGORIAS.a_nombre(parametro.categoria),
            "numero_minimo_casos": parametro.numero_minimo_casos,
            "distancia_km": parametro.distancia_km,
            "intervalo_dias": parametro.intervalo_dias,
        },
    ).rowcount


def seed(conn: Connection) -> dict[str, int]:
    """Inserta las semillas que falten y devuelve cuántas filas se insertaron por tabla."""
    anterior = detectar_semilla_anterior(conn)
    if anterior:
        raise SemillaAnteriorError(
            f"La base tiene la semilla genérica anterior ({anterior}). Recréala: docker compose down -v"
        )
    insertadas = {
        "estados": _insertar_por_nombre(
            conn,
            "estados",
            "id_estado",
            "nombre_estado",
            [{"nombre_estado": n, "descripcion_estado": d} for n, d in ESTADOS],
        ),
        "categorias_fitosanitarias": _insertar_por_nombre(
            conn,
            "categorias_fitosanitarias",
            "id_categoria",
            "nombre_categoria",
            [{"nombre_categoria": n, "es_transmisible": t} for n, t in CATEGORIAS],
        ),
        "especies": _insertar_por_nombre(
            conn, "especies", "id_especie", "nombre_especie", [{"nombre_especie": n} for n in ESPECIES]
        ),
    }
    insertadas["parametros_epidemiologicos"] = sum(_insertar_parametro(conn, p) for p in PARAMETROS)
    return insertadas


def main() -> None:
    configure_logging("INFO")
    engine = create_engine(get_database_settings().sqlalchemy_url)
    with engine.begin() as conn:
        insertadas = seed(conn)
    logger.info("Semillas aplicadas", extra={"filas_insertadas": insertadas})
    if MINIMO_DE_CASOS_PROVISIONAL:
        logger.warning("El número mínimo de casos de las alertas es PROVISIONAL (P-B02); el líder debe confirmarlo")


if __name__ == "__main__":
    main()
