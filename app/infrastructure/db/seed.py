"""Datos semilla mínimos. Idempotente: se puede correr varias veces sin duplicar ni gastar ids.

Uso:  python -m app.infrastructure.db.seed     (requiere DATABASE_URL y la migración aplicada)

Cada INSERT usa `SELECT ... WHERE NOT EXISTS` (para no consumir la secuencia si la fila ya existe)
más `ON CONFLICT DO NOTHING` (por si dos procesos siembran a la vez).
"""

import logging
from decimal import Decimal

from sqlalchemy import Connection, create_engine, text

from app.core.config import get_database_settings
from app.core.logging import configure_logging
from app.infrastructure.db.ids import PREFIJOS_ID, nombre_secuencia

logger = logging.getLogger(__name__)

ESTADOS: list[tuple[str, str]] = [
    ("Abierto", "El productor inició el caso y aún no ha sido revisado por un técnico."),
    ("En revisión", "Un técnico está revisando las evidencias del caso."),
    ("Diagnosticado", "El técnico registró su diagnóstico."),
    ("Cerrado", "El caso terminó; corre el plazo de conservación de los datos."),
]

# (nombre, es_transmisible)
CATEGORIAS: list[tuple[str, str]] = [
    ("Enfermedad fúngica", "S"),
    ("Enfermedad bacteriana", "S"),
    ("Enfermedad viral", "S"),
    ("Plaga", "S"),
    ("Deficiencia nutricional", "N"),
    ("Sin hallazgo", "N"),
]

ESPECIES: list[str] = ["Gulupa", "Maracuyá", "Granadilla", "Curuba"]

# Parámetro por categoría transmisible. Distancia e intervalo: valores de Trips en la guía (3,5 km, 30 días).
# PROVISIONAL: el número mínimo de casos no está definido por el equipo (guía, P-B02).
PARAMETRO_DEFECTO = {"numero_minimo_casos": 3, "distancia_km": Decimal("3.50"), "intervalo_dias": 30}


def _id_sql(tabla: str) -> str:
    spec = PREFIJOS_ID[tabla]
    return f"fn_generar_id('{nombre_secuencia(tabla)}', '{spec.prefijo}', '{spec.separador}', {spec.largo})"


def _insertar_por_nombre(
    conn: Connection, tabla: str, id_col: str, nombre_col: str, filas: list[dict[str, str]]
) -> int:
    columnas = list(filas[0])
    sql = text(
        f"INSERT INTO {tabla} ({id_col}, {', '.join(columnas)}) "
        f"SELECT {_id_sql(tabla)}, {', '.join(':' + c for c in columnas)} "
        f"WHERE NOT EXISTS (SELECT 1 FROM {tabla} WHERE {nombre_col} = :{nombre_col}) "
        "ON CONFLICT DO NOTHING"
    )
    return sum(conn.execute(sql, fila).rowcount for fila in filas)


def seed(conn: Connection) -> dict[str, int]:
    """Inserta las semillas que falten y devuelve cuántas filas se insertaron por tabla."""
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
    insertadas["parametros_epidemiologicos"] = conn.execute(
        text(
            "INSERT INTO parametros_epidemiologicos "
            "(id_parametro, id_categoria, numero_minimo_casos, distancia_km, intervalo_dias, fecha_actualizacion) "
            f"SELECT {_id_sql('parametros_epidemiologicos')}, c.id_categoria, "
            ":numero_minimo_casos, :distancia_km, :intervalo_dias, now() "
            "FROM categorias_fitosanitarias c "
            "WHERE c.es_transmisible = 'S' AND NOT EXISTS "
            "(SELECT 1 FROM parametros_epidemiologicos p WHERE p.id_categoria = c.id_categoria) "
            "ORDER BY c.id_categoria "
            "ON CONFLICT DO NOTHING"
        ),
        PARAMETRO_DEFECTO,
    ).rowcount
    return insertadas


def main() -> None:
    configure_logging("INFO")
    engine = create_engine(get_database_settings().sqlalchemy_url)
    with engine.begin() as conn:
        insertadas = seed(conn)
    logger.info("Semillas aplicadas", extra={"filas_insertadas": insertadas})


if __name__ == "__main__":
    main()
