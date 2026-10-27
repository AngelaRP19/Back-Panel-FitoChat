"""Semillas v2 (guía §6.5): lo que se siembra y que sea idempotente."""

import re
from decimal import Decimal

import pytest
from sqlalchemy import Connection, text

from app.core import catalogos_api as api
from app.infrastructure.db import seed as semilla
from app.infrastructure.db.seed import (
    CATEGORIAS,
    ESPECIES,
    ESTADOS,
    PARAMETROS,
    SemillaAnteriorError,
    detectar_semilla_anterior,
    seed,
)

TABLAS = ("estados", "categorias_fitosanitarias", "especies", "parametros_epidemiologicos")

# --- los datos, sin base de datos --------------------------------------------------------------------------------


def test_siete_estados_con_descripcion() -> None:
    assert [n for n, _ in ESTADOS] == [
        "Caso creado", "En análisis", "Pendiente de revisión", "En revisión", "Registrado", "Cerrado", "Descartado"
    ]  # fmt: skip
    assert all(len(d) > 10 and len(d) <= 200 for _, d in ESTADOS)  # estados.descripcion_estado es String(200)


def test_no_se_siembran_estados_descartados_por_el_equipo() -> None:
    nombres = {n for n, _ in ESTADOS}
    assert not nombres & {"Reabierto", "Análisis técnico", "Abierto", "Diagnosticado"}


def test_cinco_categorias_y_solo_trips_es_transmisible() -> None:
    assert dict(CATEGORIAS) == {
        "Botrytis": "N",
        "Roña": "N",
        "Deficiencia de magnesio": "N",
        "Trips": "S",
        "Fusarium": "N",
    }


def test_tres_especies() -> None:
    assert ESPECIES == ["Gulupa", "Granadilla", "Maracuyá"]
    assert "Curuba" not in ESPECIES


def test_no_hay_categoria_generica_plaga() -> None:
    assert "Plaga" not in {n for n, _ in CATEGORIAS}


def test_los_nombres_sembrados_son_los_del_catalogo_de_la_api() -> None:
    assert [n for n, _ in ESTADOS] == api.ESTADOS_CASO.nombres
    assert [n for n, _ in CATEGORIAS] == api.CATEGORIAS.nombres
    assert ESPECIES == api.ESPECIES.nombres


def test_un_unico_parametro_el_de_trips() -> None:
    assert len(PARAMETROS) == 1
    trips = PARAMETROS[0]
    assert trips.categoria is api.CategoriaFito.TRIPS
    assert (trips.distancia_km, trips.intervalo_dias) == (Decimal("3.50"), 30)  # decisión del equipo


def test_el_minimo_de_casos_esta_marcado_como_provisional() -> None:
    assert PARAMETROS[0].numero_minimo_casos == 3
    assert semilla.MINIMO_DE_CASOS_PROVISIONAL is True  # P-B02: el equipo aún no lo define


def test_los_parametros_solo_van_en_categorias_transmisibles() -> None:
    transmisibles = {n for n, t in CATEGORIAS if t == "S"}
    assert {api.CATEGORIAS.a_nombre(p.categoria) for p in PARAMETROS} <= transmisibles


def test_la_semilla_anterior_no_se_cruza_con_la_nueva() -> None:
    nuevos = {
        "estados": {n for n, _ in ESTADOS},
        "categorias_fitosanitarias": {n for n, _ in CATEGORIAS},
        "especies": set(ESPECIES),
    }
    for tabla, viejos in semilla.SEMILLA_ANTERIOR.items():
        assert not viejos & nuevos[tabla]


# --- contra PostgreSQL --------------------------------------------------------------------------------------------


def _conteos(conn: Connection) -> dict[str, int]:
    return {t: conn.scalar(text(f"SELECT count(*) FROM {t}")) or 0 for t in TABLAS}


def test_seed_inserta_los_datos_minimos(conn: Connection) -> None:
    insertadas = seed(conn)
    assert insertadas == {
        "estados": 7,
        "categorias_fitosanitarias": 5,
        "especies": 3,
        "parametros_epidemiologicos": 1,
    }
    assert _conteos(conn) == {
        "estados": 7,
        "categorias_fitosanitarias": 5,
        "especies": 3,
        "parametros_epidemiologicos": 1,
    }


def _numeros(ids: list[str], prefijo: str) -> list[int]:
    """Valida el formato (prefijo + 3 dígitos) y devuelve la parte numérica."""
    assert all(re.fullmatch(rf"{prefijo}\d{{3}}", i) for i in ids), ids
    return [int(i[len(prefijo) :]) for i in ids]


def test_los_ids_siguen_el_prefijo_de_cada_catalogo(conn: Connection) -> None:
    """Las secuencias no se revierten con el rollback de otros tests: se comprueba formato y orden, no el inicio."""
    seed(conn)
    estados = _numeros(conn.scalars(text("SELECT id_estado FROM estados ORDER BY id_estado")).all(), "ES")
    categorias = _numeros(
        conn.scalars(text("SELECT id_categoria FROM categorias_fitosanitarias ORDER BY 1")).all(), "CF"
    )
    especies = _numeros(conn.scalars(text("SELECT id_especie FROM especies ORDER BY 1")).all(), "EP")
    for numeros, cantidad in ((estados, 7), (categorias, 5), (especies, 3)):
        assert numeros == list(range(numeros[0], numeros[0] + cantidad))  # consecutivos, en el orden sembrado
    assert re.fullmatch(r"PAR-\d{6}", conn.scalar(text("SELECT id_parametro FROM parametros_epidemiologicos")))


def test_el_primer_estado_sembrado_es_caso_creado(conn: Connection) -> None:
    seed(conn)
    assert conn.scalar(text("SELECT nombre_estado FROM estados ORDER BY id_estado LIMIT 1")) == "Caso creado"


def test_solo_trips_es_transmisible_en_la_base(conn: Connection) -> None:
    seed(conn)
    assert conn.scalars(
        text("SELECT nombre_categoria FROM categorias_fitosanitarias WHERE es_transmisible = 'S'")
    ).all() == ["Trips"]


def test_solo_trips_tiene_parametro_epidemiologico(conn: Connection) -> None:
    seed(conn)
    fila = conn.execute(
        text(
            "SELECT c.nombre_categoria, p.numero_minimo_casos, p.distancia_km, p.intervalo_dias "
            "FROM parametros_epidemiologicos p JOIN categorias_fitosanitarias c USING (id_categoria)"
        )
    ).one()
    assert tuple(fila) == ("Trips", 3, Decimal("3.50"), 30)


def test_seed_es_idempotente_y_no_gasta_ids(conn: Connection) -> None:
    seed(conn)
    antes = _conteos(conn)
    secuencias = {
        s: conn.scalar(text(f"SELECT last_value FROM {s}"))
        for s in ("seq_estados", "seq_categorias_fitosanitarias", "seq_especies", "seq_parametros_epidemiologicos")
    }

    assert seed(conn) == dict.fromkeys(antes, 0)
    assert _conteos(conn) == antes
    for secuencia, valor in secuencias.items():
        assert conn.scalar(text(f"SELECT last_value FROM {secuencia}")) == valor


def test_seed_completa_lo_que_falte_sin_tocar_lo_que_ya_hay(conn: Connection) -> None:
    seed(conn)
    conn.execute(text("DELETE FROM parametros_epidemiologicos"))
    conn.execute(text("DELETE FROM especies WHERE nombre_especie = 'Granadilla'"))
    assert seed(conn) == {
        "estados": 0,
        "categorias_fitosanitarias": 0,
        "especies": 1,
        "parametros_epidemiologicos": 1,
    }
    assert _conteos(conn)["especies"] == 3


def test_el_parametro_se_respeta_si_el_lider_lo_edito(conn: Connection) -> None:
    seed(conn)
    conn.execute(text("UPDATE parametros_epidemiologicos SET numero_minimo_casos = 5, distancia_km = 4.00"))
    seed(conn)
    assert conn.scalar(text("SELECT numero_minimo_casos FROM parametros_epidemiologicos")) == 5


def test_no_se_siembran_preguntas(conn: Connection) -> None:
    """El texto del cuestionario no está en los documentos (P-B10): no se inventa."""
    seed(conn)
    assert conn.scalar(text("SELECT count(*) FROM preguntas")) == 0


# --- semilla genérica anterior ------------------------------------------------------------------------------------


def _sembrar_la_anterior(conn: Connection) -> None:
    conn.execute(
        text(
            "INSERT INTO estados (id_estado, nombre_estado, descripcion_estado) " "VALUES ('ES901', 'Abierto', 'viejo')"
        )
    )
    conn.execute(text("INSERT INTO especies (id_especie, nombre_especie) VALUES ('EP901', 'Curuba')"))
    conn.execute(
        text(
            "INSERT INTO categorias_fitosanitarias (id_categoria, nombre_categoria, es_transmisible) "
            "VALUES ('CF901', 'Plaga', 'S')"
        )
    )


def test_detecta_la_semilla_anterior(conn: Connection) -> None:
    assert detectar_semilla_anterior(conn) == {}
    _sembrar_la_anterior(conn)
    assert detectar_semilla_anterior(conn) == {
        "estados": ["Abierto"],
        "categorias_fitosanitarias": ["Plaga"],
        "especies": ["Curuba"],
    }


def test_seed_se_niega_a_mezclarse_con_la_semilla_anterior(conn: Connection) -> None:
    _sembrar_la_anterior(conn)
    with pytest.raises(SemillaAnteriorError, match="docker compose down -v"):
        seed(conn)
    assert conn.scalar(text("SELECT count(*) FROM estados")) == 1  # no insertó nada


# --- el comando ---------------------------------------------------------------------------------------------------


def test_main_siembra_y_avisa_que_el_minimo_de_casos_es_provisional(
    conn: Connection, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """`python -m app.infrastructure.db.seed`, con la conexión de prueba (se revierte al terminar)."""
    from contextlib import nullcontext

    class MotorDePrueba:
        def begin(self) -> object:
            return nullcontext(conn)

    monkeypatch.setattr(semilla, "create_engine", lambda *_a, **_k: MotorDePrueba())
    monkeypatch.setattr(semilla, "configure_logging", lambda *_a, **_k: None)
    with caplog.at_level("INFO"):
        semilla.main()
    assert _conteos(conn) == {
        "estados": 7,
        "categorias_fitosanitarias": 5,
        "especies": 3,
        "parametros_epidemiologicos": 1,
    }
    mensajes = [r.getMessage() for r in caplog.records]
    assert "Semillas aplicadas" in mensajes
    assert any("PROVISIONAL" in m for m in mensajes)
