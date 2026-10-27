"""La migración 0002 sube y baja sin dejar rastros y no toca lo que no debe."""

from collections.abc import Iterator

import pytest
from alembic import command
from sqlalchemy import Engine, inspect, text

from tests.db_fixtures import alembic_config

NUEVAS = {"asignaciones", "alerta_casos", "alerta_destinatarios"}


@pytest.fixture
def en_0001(migrated: Engine) -> Iterator[Engine]:
    """Deja la base en la revisión 0001 y la devuelve a `head` al terminar, pase lo que pase."""
    with migrated.begin() as conn:
        command.downgrade(alembic_config(conn), "0001")
    try:
        yield migrated
    finally:
        with migrated.begin() as conn:
            command.upgrade(alembic_config(conn), "head")


def _columnas(motor: Engine, tabla: str) -> dict[str, dict]:  # type: ignore[type-arg]
    return {c["name"]: c for c in inspect(motor).get_columns(tabla)}


def test_la_revision_actual_es_la_ultima(migrated: Engine) -> None:
    with migrated.connect() as conn:
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "0003"


def test_bajar_a_0001_quita_solo_lo_de_0002(en_0001: Engine) -> None:
    tablas = set(inspect(en_0001).get_table_names())
    assert not NUEVAS & tablas
    assert {"casos", "diagnosticos", "alertas", "auditoria"} <= tablas  # lo de 0001 sigue ahí

    assert "id_tecnico" in _columnas(en_0001, "diagnosticos")  # vuelve la columna eliminada
    for tabla, columna in (
        ("casos", "ultima_actividad"),
        ("alertas", "mensaje_correccion"),
        ("cultivos", "tipo_cultivo"),
        ("fotografias", "es_util"),
        ("eventos", "actor_tipo"),
        ("productores", "fecha_consentimiento"),
        ("auditoria", "origen"),
        ("identificacion_preliminar", "detalle_explicacion"),
    ):
        assert columna not in _columnas(en_0001, tabla)


def test_bajar_restablece_la_obligatoriedad_y_el_tipo(en_0001: Engine) -> None:
    assert _columnas(en_0001, "casos")["id_identificacion_preliminar"]["nullable"] is False
    assert _columnas(en_0001, "auditoria")["id_lider_tecnico"]["nullable"] is False
    for columna in ("resultado_tecnico", "id_categoria_confirmada", "descripcion_diagnostico", "fecha_diagnostico"):
        assert _columnas(en_0001, "diagnosticos")[columna]["nullable"] is False
    assert str(_columnas(en_0001, "alertas")["fecha_generacion_alerta"]["type"]) == "DATE"


def test_bajar_quita_las_secuencias_y_los_indices_nuevos(en_0001: Engine) -> None:
    with en_0001.connect() as conn:
        indices = set(conn.scalars(text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")))
        constraints = set(
            conn.scalars(text("SELECT conname FROM pg_constraint WHERE connamespace = 'public'::regnamespace"))
        )
    assert not {"uq_asg_caso_vigente", "ix_alerta_casos_caso"} & indices
    assert not {"ck_auditoria_origen_coherente", "ck_cultivos_tipo_cultivo_valido", "fk_aud_tecnico"} & constraints
    assert {"fk_dia_tecnico", "ix_diag_tecnico"} <= constraints | indices


def test_subir_de_nuevo_deja_la_base_igual_que_antes(migrated: Engine) -> None:
    antes = _estructura(migrated)
    with migrated.begin() as conn:
        command.downgrade(alembic_config(conn), "0001")
    with migrated.begin() as conn:
        command.upgrade(alembic_config(conn), "head")
    assert _estructura(migrated) == antes


def _estructura(motor: Engine) -> dict[str, object]:
    with motor.connect() as conn:
        return {
            "columnas": conn.execute(
                text(
                    "SELECT table_name, column_name, data_type, is_nullable, character_maximum_length "
                    "FROM information_schema.columns WHERE table_schema = 'public' ORDER BY 1, 2"
                )
            ).all(),
            "restricciones": conn.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE connamespace = 'public'::regnamespace ORDER BY 1"
                )
            ).all(),
            "indices": conn.execute(
                text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1")
            ).all(),
        }
