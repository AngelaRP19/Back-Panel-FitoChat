"""Ajustes al modelo aprobados por el equipo el 26-oct-2026 (D-01 a D-14 y D-17).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-27

Aplica exactamente la sección 6.4 de la guía: 3 tablas nuevas, 23 columnas nuevas, 1 columna eliminada
(`diagnosticos.id_tecnico`), 6 columnas que pasan a opcionales y 1 cambio de tipo. D-02, D-07 y D-16 se
rechazaron y D-15 vive en DynamoDB: no hay nada en PostgreSQL. Detalle en docs/CAMBIOS_MODELO.md.

Las tablas afectadas deben estar sin datos (RDS está vacío): las columnas nuevas obligatorias no tienen valor
por defecto salvo `ultima_actividad` y `es_util`, y el `downgrade` restablece restricciones NOT NULL.
Los CHECK llevan el nombre completo con `op.f()` para que la convención del MetaData no lo vuelva a prefijar.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tablas nuevas con id propio (prefijo en app/infrastructure/db/ids.py). Máximo: 8 dígitos (12 - 4).
SECUENCIAS = {"seq_asignaciones": 99_999_999, "seq_alerta_destinatarios": 99_999_999}


def _ts(nombre: str, *, nullable: bool = True) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(nombre, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    for secuencia, maximo in SECUENCIAS.items():
        op.execute(f"CREATE SEQUENCE IF NOT EXISTS {secuencia} MAXVALUE {maximo} NO CYCLE")

    # --- D-01: asignaciones del caso -------------------------------------------------------------------------
    op.create_table(
        "asignaciones",
        sa.Column("id_asignacion", sa.String(12), nullable=False),
        sa.Column("id_caso", sa.String(12), nullable=False),
        sa.Column("id_tecnico", sa.String(10), nullable=False),
        _ts("fecha_asignacion", nullable=False),
        _ts("fecha_fin"),
        sa.PrimaryKeyConstraint("id_asignacion", name="pk_asignaciones"),
        sa.ForeignKeyConstraint(["id_caso"], ["casos.id_caso"], name="fk_asg_caso"),
        sa.ForeignKeyConstraint(["id_tecnico"], ["tecnicos.id_tecnico"], name="fk_asg_tecnico"),
    )
    # Una sola asignación vigente (sin fecha_fin) por caso.
    op.create_index(
        "uq_asg_caso_vigente", "asignaciones", ["id_caso"], unique=True, postgresql_where=sa.text("fecha_fin IS NULL")
    )
    # El autor del diagnóstico es el técnico de la asignación vigente: diagnosticos pierde id_tecnico.
    op.drop_index("ix_diag_tecnico", table_name="diagnosticos")
    op.drop_constraint("fk_dia_tecnico", "diagnosticos", type_="foreignkey")
    op.drop_column("diagnosticos", "id_tecnico")

    # --- D-03: caso sin identificación mientras se analiza ---------------------------------------------------
    op.alter_column("casos", "id_identificacion_preliminar", existing_type=sa.String(12), nullable=True)

    # --- D-04 y D-05: diagnóstico creado vacío; la categoría y la fecha dependen del resultado -----------------
    op.alter_column("diagnosticos", "resultado_tecnico", existing_type=sa.String(20), nullable=True)
    op.alter_column("diagnosticos", "id_categoria_confirmada", existing_type=sa.String(5), nullable=True)
    op.alter_column("diagnosticos", "descripcion_diagnostico", existing_type=sa.String(1000), nullable=True)
    op.alter_column("diagnosticos", "fecha_diagnostico", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.create_check_constraint(
        op.f("ck_diagnosticos_categoria_requerida"),
        "diagnosticos",
        "resultado_tecnico IS NULL OR resultado_tecnico NOT IN ('CONFIRMADO', 'OTRA_PATOLOGIA') "
        "OR id_categoria_confirmada IS NOT NULL",
    )
    op.create_check_constraint(
        op.f("ck_diagnosticos_fecha_con_resultado"),
        "diagnosticos",
        "resultado_tecnico IS NULL OR fecha_diagnostico IS NOT NULL",
    )

    # --- D-06: ciclo de vida del caso ------------------------------------------------------------------------
    op.add_column("casos", _ts("fecha_envio_panel"))
    op.add_column("casos", _ts("fecha_cierre"))
    op.add_column("casos", sa.Column("vence_en", sa.Date(), nullable=True))
    op.add_column(
        "casos",
        sa.Column("ultima_actividad", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    # --- D-08 y D-17: gestión, comunicación y corrección de alertas --------------------------------------------
    op.alter_column(
        "alertas",
        "fecha_generacion_alerta",
        existing_type=sa.Date(),
        type_=sa.DateTime(timezone=True),
        existing_nullable=False,
        postgresql_using="fecha_generacion_alerta::timestamptz",
    )
    op.add_column("alertas", sa.Column("numero_minimo_casos", sa.Integer(), nullable=False))
    op.add_column("alertas", sa.Column("distancia_km", sa.Numeric(5, 2), nullable=False))
    op.add_column("alertas", sa.Column("intervalo_dias", sa.Integer(), nullable=False))
    op.add_column("alertas", sa.Column("recomendacion", sa.String(300), nullable=True))
    op.add_column("alertas", sa.Column("mensaje_enviado", sa.String(1000), nullable=True))
    op.add_column("alertas", sa.Column("id_lider_gestion", sa.String(10), nullable=True))
    op.add_column("alertas", _ts("fecha_confirmacion"))
    op.add_column("alertas", sa.Column("justificacion_descarte", sa.String(500), nullable=True))
    op.add_column("alertas", sa.Column("mensaje_correccion", sa.String(300), nullable=True))
    op.add_column("alertas", _ts("fecha_correccion"))
    op.add_column("alertas", sa.Column("id_lider_correccion", sa.String(10), nullable=True))
    op.create_foreign_key(
        "fk_ale_lider_gestion", "alertas", "lideres_tecnicos", ["id_lider_gestion"], ["id_lider_tecnico"]
    )
    op.create_foreign_key(
        "fk_ale_lider_correccion", "alertas", "lideres_tecnicos", ["id_lider_correccion"], ["id_lider_tecnico"]
    )

    op.create_table(
        "alerta_casos",
        sa.Column("id_alerta", sa.String(10), nullable=False),
        sa.Column("id_caso", sa.String(12), nullable=False),
        sa.PrimaryKeyConstraint("id_alerta", "id_caso", name="pk_alerta_casos"),
        sa.ForeignKeyConstraint(["id_alerta"], ["alertas.id_alerta"], name="fk_alc_alerta"),
        sa.ForeignKeyConstraint(["id_caso"], ["casos.id_caso"], name="fk_alc_caso"),
    )
    op.create_index("ix_alerta_casos_caso", "alerta_casos", ["id_caso"])

    op.create_table(
        "alerta_destinatarios",
        sa.Column("id_alerta_destinatario", sa.String(12), nullable=False),
        sa.Column("id_alerta", sa.String(10), nullable=False),
        sa.Column("id_productor", sa.String(10), nullable=False),
        sa.Column("tipo_envio", sa.String(10), nullable=False),
        sa.Column("estado_envio", sa.String(15), nullable=False),
        _ts("fecha_envio"),
        sa.Column("detalle_error", sa.String(500), nullable=True),
        sa.PrimaryKeyConstraint("id_alerta_destinatario", name="pk_alerta_destinatarios"),
        sa.UniqueConstraint("id_alerta", "id_productor", "tipo_envio", name="uq_ade_envio"),
        sa.ForeignKeyConstraint(["id_alerta"], ["alertas.id_alerta"], name="fk_ade_alerta"),
        sa.ForeignKeyConstraint(["id_productor"], ["productores.id_productor"], name="fk_ade_productor"),
        sa.CheckConstraint(
            "tipo_envio IN ('ALERTA', 'CORRECCION')", name=op.f("ck_alerta_destinatarios_tipo_envio_valido")
        ),
    )

    # --- D-09: auditoría de técnicos y del sistema -----------------------------------------------------------
    op.alter_column("auditoria", "id_lider_tecnico", existing_type=sa.String(10), nullable=True)
    op.add_column("auditoria", sa.Column("id_tecnico", sa.String(10), nullable=True))
    op.add_column("auditoria", sa.Column("origen", sa.String(10), nullable=False))
    op.create_foreign_key("fk_aud_tecnico", "auditoria", "tecnicos", ["id_tecnico"], ["id_tecnico"])
    op.create_check_constraint(
        op.f("ck_auditoria_origen_coherente"),
        "auditoria",
        "(origen = 'LIDER' AND id_lider_tecnico IS NOT NULL AND id_tecnico IS NULL) "
        "OR (origen = 'TECNICO' AND id_tecnico IS NOT NULL AND id_lider_tecnico IS NULL) "
        "OR (origen = 'SISTEMA' AND id_lider_tecnico IS NULL AND id_tecnico IS NULL)",
    )

    # --- D-10 a D-14: columnas recomendadas ------------------------------------------------------------------
    op.add_column("cultivos", sa.Column("tipo_cultivo", sa.String(15), nullable=False))
    op.create_check_constraint(
        op.f("ck_cultivos_tipo_cultivo_valido"), "cultivos", "tipo_cultivo IN ('INVERNADERO', 'CAMPO_ABIERTO')"
    )
    op.add_column("fotografias", sa.Column("es_util", sa.String(1), server_default="S", nullable=False))
    op.create_check_constraint(op.f("ck_fotografias_es_util_sn"), "fotografias", "es_util IN ('S', 'N')")
    op.add_column("productores", _ts("fecha_consentimiento"))
    op.add_column("eventos", sa.Column("actor_tipo", sa.String(15), nullable=True))
    op.add_column("eventos", sa.Column("actor_id", sa.String(10), nullable=True))
    op.add_column(
        "identificacion_preliminar",
        sa.Column("detalle_explicacion", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("identificacion_preliminar", "detalle_explicacion")
    op.drop_column("eventos", "actor_id")
    op.drop_column("eventos", "actor_tipo")
    op.drop_column("productores", "fecha_consentimiento")
    op.drop_constraint(op.f("ck_fotografias_es_util_sn"), "fotografias", type_="check")
    op.drop_column("fotografias", "es_util")
    op.drop_constraint(op.f("ck_cultivos_tipo_cultivo_valido"), "cultivos", type_="check")
    op.drop_column("cultivos", "tipo_cultivo")

    op.drop_constraint(op.f("ck_auditoria_origen_coherente"), "auditoria", type_="check")
    op.drop_constraint("fk_aud_tecnico", "auditoria", type_="foreignkey")
    op.drop_column("auditoria", "origen")
    op.drop_column("auditoria", "id_tecnico")
    op.alter_column("auditoria", "id_lider_tecnico", existing_type=sa.String(10), nullable=False)

    op.drop_table("alerta_destinatarios")
    op.drop_index("ix_alerta_casos_caso", table_name="alerta_casos")
    op.drop_table("alerta_casos")
    op.drop_constraint("fk_ale_lider_correccion", "alertas", type_="foreignkey")
    op.drop_constraint("fk_ale_lider_gestion", "alertas", type_="foreignkey")
    for columna in (
        "id_lider_correccion",
        "fecha_correccion",
        "mensaje_correccion",
        "justificacion_descarte",
        "fecha_confirmacion",
        "id_lider_gestion",
        "mensaje_enviado",
        "recomendacion",
        "intervalo_dias",
        "distancia_km",
        "numero_minimo_casos",
    ):
        op.drop_column("alertas", columna)
    op.alter_column(
        "alertas",
        "fecha_generacion_alerta",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.Date(),
        existing_nullable=False,
        postgresql_using="fecha_generacion_alerta::date",
    )

    for columna in ("ultima_actividad", "vence_en", "fecha_cierre", "fecha_envio_panel"):
        op.drop_column("casos", columna)

    op.drop_constraint(op.f("ck_diagnosticos_fecha_con_resultado"), "diagnosticos", type_="check")
    op.drop_constraint(op.f("ck_diagnosticos_categoria_requerida"), "diagnosticos", type_="check")
    op.alter_column("diagnosticos", "fecha_diagnostico", existing_type=sa.DateTime(timezone=True), nullable=False)
    op.alter_column("diagnosticos", "descripcion_diagnostico", existing_type=sa.String(1000), nullable=False)
    op.alter_column("diagnosticos", "id_categoria_confirmada", existing_type=sa.String(5), nullable=False)
    op.alter_column("diagnosticos", "resultado_tecnico", existing_type=sa.String(20), nullable=False)
    op.alter_column("casos", "id_identificacion_preliminar", existing_type=sa.String(12), nullable=False)

    op.add_column("diagnosticos", sa.Column("id_tecnico", sa.String(10), nullable=False))
    op.create_foreign_key("fk_dia_tecnico", "diagnosticos", "tecnicos", ["id_tecnico"], ["id_tecnico"])
    op.create_index("ix_diag_tecnico", "diagnosticos", ["id_tecnico"])

    op.drop_index("uq_asg_caso_vigente", table_name="asignaciones")
    op.drop_table("asignaciones")
    for secuencia in SECUENCIAS:
        op.execute(f"DROP SEQUENCE IF EXISTS {secuencia}")
