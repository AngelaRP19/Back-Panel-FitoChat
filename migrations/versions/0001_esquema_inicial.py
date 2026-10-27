"""Esquema inicial: extensiones, secuencias de ids, tablas e índices.

Revision ID: 0001
Revises:
Create Date: 2026-10-21

Orden: extensiones -> secuencias y fn_generar_id -> tablas (en orden de dependencia) -> índices.
"""

from collections.abc import Sequence

import geoalchemy2
import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

from app.core.config import get_database_settings
from app.infrastructure.db.ids import PREFIJOS_ID, nombre_secuencia

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Dimensión de los embeddings al momento de migrar (EMBEDDING_DIM, por defecto 1024).
# Cambiarla después exige una migración nueva y recalcular TODOS los vectores.
EMBEDDING_DIM = get_database_settings().embedding_dim

FN_GENERAR_ID = """
CREATE OR REPLACE FUNCTION fn_generar_id(p_secuencia text, p_prefijo text, p_separador text, p_largo integer)
RETURNS varchar
LANGUAGE plpgsql
VOLATILE
AS $$
DECLARE
    v_digitos integer := p_largo - length(p_prefijo) - length(p_separador);
    v_numero  bigint  := nextval(p_secuencia::regclass);
BEGIN
    IF length(v_numero::text) > v_digitos THEN
        RAISE EXCEPTION 'La secuencia % se agotó: % no cabe en % dígitos', p_secuencia, v_numero, v_digitos;
    END IF;
    RETURN p_prefijo || p_separador || lpad(v_numero::text, v_digitos, '0');
END;
$$;
"""


def _ubicacion(latitud: str, longitud: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        "ubicacion",
        geoalchemy2.Geometry("POINT", srid=4326, spatial_index=False),
        sa.Computed(
            f"ST_SetSRID(ST_MakePoint({longitud}::double precision, {latitud}::double precision), 4326)",
            persisted=True,
        ),
        nullable=False,
    )


def _ts(name: str, nullable: bool = False) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


# Los CHECK llevan el nombre corto: la convención de nombres del MetaData (ck_<tabla>_<nombre>) se aplica
# también en las operaciones de Alembic, así que un nombre completo quedaría con el prefijo duplicado.


def upgrade() -> None:
    # 1. Extensiones (en RDS requieren el rol rds_superuser, que tiene el usuario maestro).
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 2. Secuencias por tabla (con tope según los dígitos disponibles) y función generadora.
    for tabla, spec in PREFIJOS_ID.items():
        op.execute(f"CREATE SEQUENCE IF NOT EXISTS {nombre_secuencia(tabla)} MAXVALUE {spec.valor_maximo} NO CYCLE")
    op.execute(FN_GENERAR_ID)

    # 3. Tablas
    op.create_table(
        "categorias_fitosanitarias",
        sa.Column("id_categoria", sa.String(5), nullable=False),
        sa.Column("nombre_categoria", sa.String(50), nullable=False),
        sa.Column("es_transmisible", sa.String(1), nullable=False),
        sa.PrimaryKeyConstraint("id_categoria", name="pk_categorias"),
        sa.UniqueConstraint("nombre_categoria", name="uq_cat_nombre"),
        sa.CheckConstraint("es_transmisible IN ('S', 'N')", name="es_transmisible_sn"),
    )
    op.create_table(
        "estados",
        sa.Column("id_estado", sa.String(5), nullable=False),
        sa.Column("nombre_estado", sa.String(50), nullable=False),
        sa.Column("descripcion_estado", sa.String(200), nullable=False),
        sa.PrimaryKeyConstraint("id_estado", name="pk_estados"),
        sa.UniqueConstraint("nombre_estado", name="uq_est_nombre"),
    )
    op.create_table(
        "especies",
        sa.Column("id_especie", sa.String(5), nullable=False),
        sa.Column("nombre_especie", sa.String(50), nullable=False),
        sa.PrimaryKeyConstraint("id_especie", name="pk_especies"),
        sa.UniqueConstraint("nombre_especie", name="uq_esp_nombre"),
    )
    op.create_table(
        "productores",
        sa.Column("id_productor", sa.String(10), nullable=False),
        sa.Column("nombre_productor", sa.String(100), nullable=False),
        sa.Column("telefono_productor", sa.String(15), nullable=False),
        sa.Column("estado_cuenta_productor", sa.String(15), nullable=False),
        sa.Column("fecha_registro_productor", sa.Date(), nullable=False),
        sa.PrimaryKeyConstraint("id_productor", name="pk_productores"),
        sa.UniqueConstraint("telefono_productor", name="uq_prod_telefono"),
    )
    op.create_table(
        "fincas",
        sa.Column("id_finca", sa.String(10), nullable=False),
        sa.Column("nombre_finca", sa.String(100), nullable=False),
        sa.Column("vereda_sector_finca", sa.String(100), nullable=False),
        sa.Column("latitud_finca", sa.Numeric(9, 6), nullable=False),
        sa.Column("longitud_finca", sa.Numeric(9, 6), nullable=False),
        sa.Column("id_productor", sa.String(10), nullable=False),
        _ubicacion("latitud_finca", "longitud_finca"),
        sa.PrimaryKeyConstraint("id_finca", name="pk_fincas"),
        sa.ForeignKeyConstraint(["id_productor"], ["productores.id_productor"], name="fk_fin_productor"),
    )
    op.create_table(
        "cultivos",
        sa.Column("id_cultivo", sa.String(10), nullable=False),
        sa.Column("nombre_cultivo", sa.String(100), nullable=False),
        sa.Column("latitud_cultivo", sa.Numeric(9, 6), nullable=False),
        sa.Column("longitud_cultivo", sa.Numeric(9, 6), nullable=False),
        sa.Column("id_finca", sa.String(10), nullable=False),
        _ubicacion("latitud_cultivo", "longitud_cultivo"),
        sa.PrimaryKeyConstraint("id_cultivo", name="pk_cultivos"),
        sa.ForeignKeyConstraint(["id_finca"], ["fincas.id_finca"], name="fk_cul_finca"),
    )
    op.create_table(
        "cultivo_especie",
        sa.Column("id_cultivo", sa.String(10), nullable=False),
        sa.Column("id_especie", sa.String(5), nullable=False),
        sa.PrimaryKeyConstraint("id_cultivo", "id_especie", name="pk_cultivo_especie"),
        sa.ForeignKeyConstraint(["id_cultivo"], ["cultivos.id_cultivo"], name="fk_ce_cultivo"),
        sa.ForeignKeyConstraint(["id_especie"], ["especies.id_especie"], name="fk_ce_especie"),
    )
    op.create_table(
        "identificacion_preliminar",
        sa.Column("id_identificacion", sa.String(12), nullable=False),
        sa.Column("tipo_resultado", sa.String(30), nullable=False),
        sa.Column("explicacion_resultado", sa.String(2000), nullable=False),
        _ts("fecha_analisis"),
        sa.PrimaryKeyConstraint("id_identificacion", name="pk_identificacion"),
    )
    op.create_table(
        "coincidencias_categoria",
        sa.Column("id_identificacion", sa.String(12), nullable=False),
        sa.Column("id_categoria", sa.String(5), nullable=False),
        sa.Column("porcentaje_coincidencia", sa.Numeric(5, 2), nullable=False),
        sa.PrimaryKeyConstraint("id_identificacion", "id_categoria", name="pk_coincidencias"),
        sa.ForeignKeyConstraint(
            ["id_identificacion"], ["identificacion_preliminar.id_identificacion"], name="fk_coi_identificacion"
        ),
        sa.ForeignKeyConstraint(["id_categoria"], ["categorias_fitosanitarias.id_categoria"], name="fk_coi_categoria"),
        sa.CheckConstraint(
            "porcentaje_coincidencia BETWEEN 0 AND 100", name="porcentaje_rango"
        ),
    )
    op.create_table(
        "casos",
        sa.Column("id_caso", sa.String(12), nullable=False),
        sa.Column("id_cultivo", sa.String(10), nullable=False),
        sa.Column("id_especie", sa.String(5), nullable=False),
        sa.Column("id_identificacion_preliminar", sa.String(12), nullable=False),
        sa.Column("id_estado", sa.String(5), nullable=False),
        sa.Column("descripcion_zona_afectada", sa.String(300), nullable=True),
        _ts("fecha_creacion_caso"),
        sa.Column("advertencia_inactividad_enviada", sa.String(1), server_default="N", nullable=False),
        sa.PrimaryKeyConstraint("id_caso", name="pk_casos"),
        sa.ForeignKeyConstraint(
            ["id_cultivo", "id_especie"],
            ["cultivo_especie.id_cultivo", "cultivo_especie.id_especie"],
            name="fk_cas_cul_esp",
        ),
        sa.ForeignKeyConstraint(
            ["id_identificacion_preliminar"], ["identificacion_preliminar.id_identificacion"], name="fk_cas_iden"
        ),
        sa.ForeignKeyConstraint(["id_estado"], ["estados.id_estado"], name="fk_cas_estado"),
        sa.CheckConstraint(
            "advertencia_inactividad_enviada IN ('S', 'N')", name="advertencia_inactividad_enviada_sn"
        ),
    )
    op.create_table(
        "eventos",
        sa.Column("id_evento", sa.String(12), nullable=False),
        sa.Column("id_caso", sa.String(12), nullable=False),
        sa.Column("tipo_evento", sa.String(30), nullable=False),
        sa.Column("descripcion_evento", sa.String(500), nullable=False),
        _ts("fecha_evento"),
        sa.PrimaryKeyConstraint("id_evento", name="pk_eventos"),
        sa.ForeignKeyConstraint(["id_caso"], ["casos.id_caso"], name="fk_eve_caso"),
    )
    op.create_table(
        "tecnicos",
        sa.Column("id_tecnico", sa.String(10), nullable=False),
        sa.Column("nombre_tecnico", sa.String(100), nullable=False),
        sa.Column("correo_tecnico", sa.String(100), nullable=False),
        sa.Column("celular_tecnico", sa.String(15), nullable=False),
        sa.Column("contrasena_tecnico", sa.String(255), nullable=False),  # solo hashes Argon2
        sa.Column("estado_cuenta_tecnico", sa.String(15), nullable=False),
        sa.Column("fecha_registro_tecnico", sa.Date(), nullable=False),
        sa.Column("cognito_sub", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id_tecnico", name="pk_tecnico"),
        sa.UniqueConstraint("correo_tecnico", name="uq_tec_correo"),
        sa.UniqueConstraint("celular_tecnico", name="uq_tec_celular"),
        sa.UniqueConstraint("cognito_sub", name="uq_tec_cognito_sub"),
    )
    op.create_table(
        "lideres_tecnicos",
        sa.Column("id_lider_tecnico", sa.String(10), nullable=False),
        sa.Column("nombre_lider_tecnico", sa.String(100), nullable=False),
        sa.Column("correo_lider_tecnico", sa.String(100), nullable=False),
        sa.Column("celular_lider_tecnico", sa.String(15), nullable=False),
        sa.Column("contrasena_lider_tecnico", sa.String(255), nullable=False),  # solo hashes Argon2
        sa.Column("estado_cuenta_lider_tecnico", sa.String(15), nullable=False),
        sa.Column("cognito_sub", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id_lider_tecnico", name="pk_lider_tecnico"),
        sa.UniqueConstraint("correo_lider_tecnico", name="uq_lid_tec_correo"),
        sa.UniqueConstraint("celular_lider_tecnico", name="uq_lid_tec_celular"),
        sa.UniqueConstraint("cognito_sub", name="uq_lid_tec_cognito_sub"),
    )
    op.create_table(
        "diagnosticos",
        sa.Column("id_diagnostico", sa.String(12), nullable=False),
        sa.Column("id_caso", sa.String(12), nullable=False),
        sa.Column("id_tecnico", sa.String(10), nullable=False),
        sa.Column("resultado_tecnico", sa.String(20), nullable=False),
        sa.Column("id_categoria_confirmada", sa.String(5), nullable=False),
        sa.Column("patologia_alternativa", sa.String(100), nullable=True),
        sa.Column("motivo_visita", sa.String(500), nullable=True),
        sa.Column("descripcion_diagnostico", sa.String(1000), nullable=False),
        sa.Column("observaciones_diagnostico", sa.String(1000), nullable=True),
        _ts("fecha_diagnostico"),
        sa.PrimaryKeyConstraint("id_diagnostico", name="pk_diagnosticos"),
        sa.UniqueConstraint("id_caso", name="uq_dia_caso"),
        sa.ForeignKeyConstraint(["id_caso"], ["casos.id_caso"], name="fk_dia_caso"),
        sa.ForeignKeyConstraint(["id_tecnico"], ["tecnicos.id_tecnico"], name="fk_dia_tecnico"),
        sa.ForeignKeyConstraint(
            ["id_categoria_confirmada"], ["categorias_fitosanitarias.id_categoria"], name="fk_dia_categoria"
        ),
    )
    op.create_table(
        "preguntas",
        sa.Column("id_pregunta", sa.String(10), nullable=False),
        sa.Column("texto_pregunta", sa.String(300), nullable=False),
        sa.PrimaryKeyConstraint("id_pregunta", name="pk_preguntas"),
    )
    op.create_table(
        "respuestas",
        sa.Column("id_respuesta", sa.String(12), nullable=False),
        sa.Column("id_pregunta", sa.String(10), nullable=False),
        sa.Column("id_diagnostico", sa.String(12), nullable=False),
        sa.Column("texto_respuesta", sa.String(500), nullable=False),
        _ts("fecha_respuesta"),
        sa.PrimaryKeyConstraint("id_respuesta", name="pk_respuestas"),
        sa.ForeignKeyConstraint(["id_pregunta"], ["preguntas.id_pregunta"], name="fk_res_pregunta"),
        sa.ForeignKeyConstraint(["id_diagnostico"], ["diagnosticos.id_diagnostico"], name="fk_res_diagnostico"),
    )
    op.create_table(
        "fotografias",
        sa.Column("id_fotografia", sa.String(12), nullable=False),
        sa.Column("id_respuesta", sa.String(12), nullable=False),
        sa.Column("tipo_evidencia", sa.String(20), nullable=False),
        sa.Column("ruta_fotografia", sa.String(500), nullable=False),  # clave del objeto en S3
        _ts("fecha_envio_fotografia"),
        sa.PrimaryKeyConstraint("id_fotografia", name="pk_fotografias"),
        sa.ForeignKeyConstraint(["id_respuesta"], ["respuestas.id_respuesta"], name="fk_fot_res"),
    )
    op.create_table(
        "parametros_epidemiologicos",
        sa.Column("id_parametro", sa.String(10), nullable=False),
        sa.Column("id_categoria", sa.String(5), nullable=False),
        sa.Column("numero_minimo_casos", sa.Integer(), nullable=False),
        sa.Column("distancia_km", sa.Numeric(5, 2), nullable=False),
        sa.Column("intervalo_dias", sa.Integer(), nullable=False),
        _ts("fecha_actualizacion"),
        sa.PrimaryKeyConstraint("id_parametro", name="pk_parametros"),
        sa.UniqueConstraint("id_categoria", name="uq_par_categoria"),
        sa.ForeignKeyConstraint(["id_categoria"], ["categorias_fitosanitarias.id_categoria"], name="fk_par_categoria"),
        sa.CheckConstraint(
            "numero_minimo_casos BETWEEN 1 AND 999", name="numero_minimo_casos_rango"
        ),
        sa.CheckConstraint("intervalo_dias BETWEEN 1 AND 999", name="intervalo_dias_rango"),
    )
    op.create_table(
        "alertas",
        sa.Column("id_alerta", sa.String(10), nullable=False),
        sa.Column("id_categoria", sa.String(5), nullable=False),
        sa.Column("estado_alerta", sa.String(15), nullable=False),
        sa.Column("fecha_generacion_alerta", sa.Date(), nullable=False),
        sa.Column("fecha_comunicacion_alerta", sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint("id_alerta", name="pk_alertas"),
        sa.ForeignKeyConstraint(["id_categoria"], ["categorias_fitosanitarias.id_categoria"], name="fk_ale_categoria"),
    )
    op.create_table(
        "auditoria",
        sa.Column("id_auditoria", sa.String(12), nullable=False),
        sa.Column("id_lider_tecnico", sa.String(10), nullable=False),
        sa.Column("accion", sa.String(20), nullable=False),
        sa.Column("entidad_afectada", sa.String(50), nullable=False),
        sa.Column("descripcion", sa.String(500), nullable=False),
        sa.Column("valor_anterior", sa.String(1000), nullable=True),
        sa.Column("valor_nuevo", sa.String(1000), nullable=True),
        sa.Column("resultado", sa.String(10), nullable=False),
        _ts("fecha_auditoria"),
        sa.PrimaryKeyConstraint("id_auditoria", name="pk_auditoria"),
        sa.ForeignKeyConstraint(["id_lider_tecnico"], ["lideres_tecnicos.id_lider_tecnico"], name="fk_aud_lider"),
    )

    # Extensión de arquitectura
    op.create_table(
        "mensajes_whatsapp",
        sa.Column("id_mensaje", sa.String(12), nullable=False),
        sa.Column("wamid", sa.String(128), nullable=False),
        sa.Column("telefono", sa.String(15), nullable=False),
        sa.Column("tipo", sa.String(20), nullable=False),
        sa.Column("contenido", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("estado_procesamiento", sa.String(15), nullable=False),
        _ts("fecha_recepcion"),
        sa.Column("id_productor", sa.String(10), nullable=True),
        sa.PrimaryKeyConstraint("id_mensaje", name="pk_mensajes_whatsapp"),
        sa.UniqueConstraint("wamid", name="uq_msg_wamid"),
        sa.ForeignKeyConstraint(["id_productor"], ["productores.id_productor"], name="fk_msg_productor"),
    )
    op.create_table(
        "conocimiento_embeddings",
        sa.Column("id_conocimiento", sa.String(12), nullable=False),
        sa.Column("id_categoria", sa.String(5), nullable=True),
        sa.Column("fuente", sa.String(200), nullable=False),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(EMBEDDING_DIM), nullable=False),
        sa.PrimaryKeyConstraint("id_conocimiento", name="pk_conocimiento_embeddings"),
        sa.ForeignKeyConstraint(["id_categoria"], ["categorias_fitosanitarias.id_categoria"], name="fk_con_categoria"),
    )

    # 4. Índices
    op.create_index("ix_fincas_productor", "fincas", ["id_productor"])
    op.create_index("ix_fincas_ubicacion", "fincas", ["ubicacion"], postgresql_using="gist")
    op.create_index("ix_cultivos_finca", "cultivos", ["id_finca"])
    op.create_index("ix_cultivos_ubicacion", "cultivos", ["ubicacion"], postgresql_using="gist")
    op.create_index("ix_cult_esp_especie", "cultivo_especie", ["id_especie"])
    op.create_index("ix_coinc_categoria", "coincidencias_categoria", ["id_categoria"])
    op.create_index("ix_casos_cult_esp", "casos", ["id_cultivo", "id_especie"])
    op.create_index("ix_casos_identificacion", "casos", ["id_identificacion_preliminar"])
    op.create_index("ix_casos_estado", "casos", ["id_estado"])
    op.create_index("ix_eventos_caso", "eventos", ["id_caso", "fecha_evento"])
    op.create_index("ix_diag_tecnico", "diagnosticos", ["id_tecnico"])
    op.create_index("ix_diag_categoria", "diagnosticos", ["id_categoria_confirmada"])
    op.create_index("ix_fotos_respuesta", "fotografias", ["id_respuesta"])
    op.create_index("ix_alertas_categoria", "alertas", ["id_categoria"])
    op.create_index("ix_auditoria_lider", "auditoria", ["id_lider_tecnico", "fecha_auditoria"])
    op.create_index("ix_auditoria_entidad", "auditoria", ["entidad_afectada"])
    op.create_index("ix_msg_telefono_fecha", "mensajes_whatsapp", ["telefono", "fecha_recepcion"])
    op.create_index("ix_con_categoria", "conocimiento_embeddings", ["id_categoria"])
    op.create_index(
        "ix_con_embedding_hnsw",
        "conocimiento_embeddings",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    # Al borrar las tablas se borran sus índices y restricciones.
    for tabla in (
        "conocimiento_embeddings",
        "mensajes_whatsapp",
        "auditoria",
        "alertas",
        "parametros_epidemiologicos",
        "fotografias",
        "respuestas",
        "preguntas",
        "diagnosticos",
        "lideres_tecnicos",
        "tecnicos",
        "eventos",
        "casos",
        "coincidencias_categoria",
        "identificacion_preliminar",
        "cultivo_especie",
        "cultivos",
        "fincas",
        "productores",
        "especies",
        "estados",
        "categorias_fitosanitarias",
    ):
        op.drop_table(tabla)

    op.execute("DROP FUNCTION IF EXISTS fn_generar_id(text, text, text, integer)")
    for tabla in PREFIJOS_ID:
        op.execute(f"DROP SEQUENCE IF EXISTS {nombre_secuencia(tabla)}")
    # Las extensiones postgis y vector NO se eliminan: pueden estar en uso por otros esquemas
    # (y en RDS su borrado es una decisión del administrador de la base).
