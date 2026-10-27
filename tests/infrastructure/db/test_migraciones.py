from alembic import command
from sqlalchemy import Engine, inspect, text

from app.infrastructure.db.base import Base
from app.infrastructure.db.ids import PREFIJOS_ID, nombre_secuencia
from tests.db_fixtures import alembic_config

TABLAS = set(Base.metadata.tables)

# Nombres exactos del modelo físico (diagrama + prompt).
RESTRICCIONES_DEL_MODELO = {
    "pk_categorias", "pk_estados", "pk_especies", "pk_productores", "pk_fincas", "pk_cultivos",
    "pk_cultivo_especie", "pk_identificacion", "pk_coincidencias", "pk_casos", "pk_eventos", "pk_tecnico",
    "pk_lider_tecnico", "pk_diagnosticos", "pk_preguntas", "pk_respuestas", "pk_fotografias", "pk_parametros",
    "pk_alertas", "pk_auditoria",
    "uq_cat_nombre", "uq_est_nombre", "uq_esp_nombre", "uq_prod_telefono", "uq_tec_correo", "uq_tec_celular",
    "uq_lid_tec_correo", "uq_lid_tec_celular", "uq_dia_caso", "uq_par_categoria",
    "fk_fin_productor", "fk_cul_finca", "fk_ce_cultivo", "fk_ce_especie", "fk_coi_identificacion",
    "fk_coi_categoria", "fk_cas_cul_esp", "fk_cas_iden", "fk_cas_estado", "fk_eve_caso", "fk_dia_caso",
    "fk_dia_tecnico", "fk_dia_categoria", "fk_res_pregunta", "fk_res_diagnostico", "fk_fot_res",
    "fk_par_categoria", "fk_ale_categoria", "fk_aud_lider",
}  # fmt: skip

INDICES_DEL_MODELO = {
    "ix_fincas_productor", "ix_cultivos_finca", "ix_cult_esp_especie", "ix_coinc_categoria", "ix_casos_cult_esp",
    "ix_casos_identificacion", "ix_casos_estado", "ix_eventos_caso", "ix_diag_tecnico", "ix_diag_categoria",
    "ix_fotos_respuesta", "ix_alertas_categoria", "ix_auditoria_lider", "ix_auditoria_entidad",
    # agregados
    "ix_fincas_ubicacion", "ix_cultivos_ubicacion", "ix_msg_telefono_fecha", "ix_con_embedding_hnsw",
}  # fmt: skip


def _tablas(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names()) & TABLAS


def test_upgrade_crea_todo_y_downgrade_lo_quita(migrated: Engine) -> None:
    assert _tablas(migrated) == TABLAS

    with migrated.begin() as conn:
        command.downgrade(alembic_config(conn), "base")
    assert _tablas(migrated) == set()
    with migrated.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM pg_proc WHERE proname = 'fn_generar_id'")) == 0
        assert conn.scalar(text("SELECT count(*) FROM pg_class WHERE relkind = 'S' AND relname LIKE 'seq_%'")) == 0

    with migrated.begin() as conn:
        command.upgrade(alembic_config(conn), "head")
    assert _tablas(migrated) == TABLAS


def test_extensiones_instaladas(migrated: Engine) -> None:
    with migrated.connect() as conn:
        extensiones = set(conn.scalars(text("SELECT extname FROM pg_extension")))
    assert {"postgis", "vector"} <= extensiones


def test_nombres_de_restricciones_del_modelo(migrated: Engine) -> None:
    with migrated.connect() as conn:
        nombres = set(
            conn.scalars(text("SELECT conname FROM pg_constraint WHERE connamespace = 'public'::regnamespace"))
        )
    assert RESTRICCIONES_DEL_MODELO - nombres == set()


def test_nombres_de_indices(migrated: Engine) -> None:
    with migrated.connect() as conn:
        nombres = set(conn.scalars(text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")))
    assert INDICES_DEL_MODELO - nombres == set()


def test_indices_gist_y_hnsw(migrated: Engine) -> None:
    with migrated.connect() as conn:
        defs = dict(conn.execute(text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'")).all())
    assert "USING gist (ubicacion)" in defs["ix_fincas_ubicacion"]
    assert "USING gist (ubicacion)" in defs["ix_cultivos_ubicacion"]
    assert "USING hnsw (embedding vector_cosine_ops)" in defs["ix_con_embedding_hnsw"]


def test_una_secuencia_por_tabla(migrated: Engine) -> None:
    with migrated.connect() as conn:
        secuencias = set(conn.scalars(text("SELECT relname FROM pg_class WHERE relkind = 'S'")))
    assert {nombre_secuencia(t) for t in PREFIJOS_ID} <= secuencias


def test_modelos_y_migracion_coinciden(migrated: Engine) -> None:
    """autogenerate no debe encontrar diferencias entre los modelos y la base migrada."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.infrastructure.db.alembic_support import include_object

    with migrated.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"include_object": include_object, "compare_type": True})
        diferencias = compare_metadata(ctx, Base.metadata)
    assert diferencias == []
