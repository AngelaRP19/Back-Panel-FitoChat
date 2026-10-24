from sqlalchemy import Connection, text

from app.infrastructure.db.seed import CATEGORIAS, ESPECIES, ESTADOS, seed


def _conteos(conn: Connection) -> dict[str, int]:
    return {
        tabla: conn.scalar(text(f"SELECT count(*) FROM {tabla}")) or 0
        for tabla in ("estados", "categorias_fitosanitarias", "especies", "parametros_epidemiologicos")
    }


def test_seed_inserta_los_datos_minimos(conn: Connection) -> None:
    insertadas = seed(conn)
    transmisibles = sum(1 for _, t in CATEGORIAS if t == "S")
    assert insertadas == {
        "estados": len(ESTADOS),
        "categorias_fitosanitarias": len(CATEGORIAS),
        "especies": len(ESPECIES),
        "parametros_epidemiologicos": transmisibles,
    }
    sin_parametro = conn.scalar(
        text(
            "SELECT count(*) FROM categorias_fitosanitarias c "
            "LEFT JOIN parametros_epidemiologicos p ON p.id_categoria = c.id_categoria "
            "WHERE c.es_transmisible = 'S' AND p.id_parametro IS NULL"
        )
    )
    assert sin_parametro == 0


def test_seed_es_idempotente_y_no_gasta_ids(conn: Connection) -> None:
    seed(conn)
    antes = _conteos(conn)
    ultimo_estado = conn.scalar(text("SELECT last_value FROM seq_estados"))

    assert seed(conn) == dict.fromkeys(antes, 0)
    assert _conteos(conn) == antes
    assert conn.scalar(text("SELECT last_value FROM seq_estados")) == ultimo_estado


def test_ids_de_semillas_con_prefijo(conn: Connection) -> None:
    seed(conn)
    ids = conn.scalars(text("SELECT id_especie FROM especies ORDER BY id_especie")).all()
    assert all(i.startswith("EP") and len(i) == 5 for i in ids)
