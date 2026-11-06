"""Forma del modelo según los ajustes aprobados (guía §6.4), comprobada sobre `Base.metadata`: sin base de datos."""

import pytest
from sqlalchemy import Date, DateTime, Index, Integer, Numeric, String, Table
from sqlalchemy.dialects.postgresql import JSONB

from app.infrastructure.db.base import Base
from app.infrastructure.db.ids import PREFIJOS_ID


def tabla(nombre: str) -> Table:
    return Base.metadata.tables[nombre]


# (tabla, columna, tipo, longitud, nulable): las 23 columnas nuevas en tablas existentes.
COLUMNAS_NUEVAS = [
    # D-06
    ("casos", "fecha_envio_panel", DateTime, None, True),
    ("casos", "fecha_cierre", DateTime, None, True),
    ("casos", "vence_en", Date, None, True),
    ("casos", "ultima_actividad", DateTime, None, False),
    # D-08
    ("alertas", "numero_minimo_casos", Integer, None, False),
    ("alertas", "distancia_km", Numeric, None, False),
    ("alertas", "intervalo_dias", Integer, None, False),
    ("alertas", "recomendacion", String, 300, True),
    ("alertas", "mensaje_enviado", String, 1000, True),
    ("alertas", "id_lider_gestion", String, 10, True),
    ("alertas", "fecha_confirmacion", DateTime, None, True),
    ("alertas", "justificacion_descarte", String, 500, True),
    # D-17
    ("alertas", "mensaje_correccion", String, 300, True),
    ("alertas", "fecha_correccion", DateTime, None, True),
    ("alertas", "id_lider_correccion", String, 10, True),
    # D-09
    ("auditoria", "id_tecnico", String, 10, True),
    ("auditoria", "origen", String, 10, False),
    # D-10 a D-14
    ("cultivos", "tipo_cultivo", String, 15, False),
    ("fotografias", "es_util", String, 1, False),
    ("productores", "fecha_consentimiento", DateTime, None, True),
    ("eventos", "actor_tipo", String, 15, True),
    ("eventos", "actor_id", String, 10, True),
    ("identificacion_preliminar", "detalle_explicacion", JSONB, None, True),
]


def test_son_23_columnas_nuevas() -> None:
    assert len(COLUMNAS_NUEVAS) == 23


@pytest.mark.parametrize(("nombre_tabla", "columna", "tipo", "longitud", "nulable"), COLUMNAS_NUEVAS)
def test_columna_nueva(nombre_tabla: str, columna: str, tipo: type, longitud: int | None, nulable: bool) -> None:
    col = tabla(nombre_tabla).columns[columna]
    assert isinstance(col.type, tipo)
    if longitud is not None:
        assert col.type.length == longitud  # type: ignore[attr-defined]
    assert col.nullable is nulable


def test_las_fechas_nuevas_llevan_zona_horaria() -> None:
    for nombre_tabla, columna, tipo, _, _ in COLUMNAS_NUEVAS:
        if tipo is DateTime:
            assert tabla(nombre_tabla).columns[columna].type.timezone is True  # type: ignore[attr-defined]  # noqa: SIM300


def test_valores_por_defecto() -> None:
    assert tabla("fotografias").columns["es_util"].server_default.arg == "S"  # type: ignore[union-attr]
    assert "now()" in str(tabla("casos").columns["ultima_actividad"].server_default.arg)  # type: ignore[union-attr]


# --- tres tablas nuevas ------------------------------------------------------------------------------------------


def test_son_25_tablas_con_las_3_nuevas() -> None:
    assert len(Base.metadata.tables) == 25
    assert {"asignaciones", "alerta_casos", "alerta_destinatarios"} <= set(Base.metadata.tables)


def test_asignaciones() -> None:
    cols = tabla("asignaciones").columns
    assert list(cols.keys()) == ["id_asignacion", "id_caso", "id_tecnico", "fecha_asignacion", "fecha_fin"]
    assert cols["id_asignacion"].type.length == 12  # type: ignore[attr-defined]
    assert [c.nullable for c in cols] == [False, False, False, False, True]
    assert PREFIJOS_ID["asignaciones"].prefijo == "ASG"


def test_asignaciones_indice_unico_parcial_por_caso() -> None:
    indices = {i.name: i for i in tabla("asignaciones").indexes}
    vigente: Index = indices["uq_asg_caso_vigente"]
    assert vigente.unique
    assert [c.name for c in vigente.columns] == ["id_caso"]
    assert str(vigente.dialect_options["postgresql"]["where"]) == "fecha_fin IS NULL"


def test_alerta_casos_tiene_clave_compuesta() -> None:
    assert [c.name for c in tabla("alerta_casos").primary_key.columns] == ["id_alerta", "id_caso"]


def test_alerta_destinatarios() -> None:
    t = tabla("alerta_destinatarios")
    assert set(t.columns.keys()) == {
        "id_alerta_destinatario", "id_alerta", "id_productor", "tipo_envio",
        "estado_envio", "fecha_envio", "detalle_error",
    }  # fmt: skip
    unicas = {tuple(c.name for c in u.columns) for u in t.constraints if u.__class__.__name__ == "UniqueConstraint"}
    assert ("id_alerta", "id_productor", "tipo_envio") in unicas
    assert PREFIJOS_ID["alerta_destinatarios"].prefijo == "ADE"
    assert PREFIJOS_ID["alerta_destinatarios"].largo == 12


# --- una columna eliminada ---------------------------------------------------------------------------------------


def test_diagnosticos_ya_no_tiene_id_tecnico() -> None:
    t = tabla("diagnosticos")
    assert "id_tecnico" not in t.columns
    assert not any("tecnicos" in {e.column.table.name for e in fk.elements} for fk in t.foreign_key_constraints)
    assert "ix_diag_tecnico" not in {i.name for i in t.indexes}


def test_diagnosticos_conserva_un_diagnostico_por_caso() -> None:
    unicas = {tuple(c.name for c in u.columns) for u in tabla("diagnosticos").constraints if u.name == "uq_dia_caso"}
    assert unicas == {("id_caso",)}


# --- seis columnas que pasan a opcionales y un cambio de tipo ----------------------------------------------------

NULABLES = [
    ("casos", "id_identificacion_preliminar"),  # D-03
    ("diagnosticos", "id_categoria_confirmada"),  # D-04
    ("diagnosticos", "descripcion_diagnostico"),  # D-04
    ("diagnosticos", "resultado_tecnico"),  # D-05
    ("diagnosticos", "fecha_diagnostico"),  # D-05
    ("auditoria", "id_lider_tecnico"),  # D-09
]


@pytest.mark.parametrize(("nombre_tabla", "columna"), NULABLES)
def test_pasa_a_opcional(nombre_tabla: str, columna: str) -> None:
    assert tabla(nombre_tabla).columns[columna].nullable is True


def test_son_6_las_que_pasan_a_opcionales() -> None:
    assert len(NULABLES) == 6


def test_fecha_de_generacion_de_la_alerta_ahora_lleva_hora() -> None:
    tipo = tabla("alertas").columns["fecha_generacion_alerta"].type
    assert isinstance(tipo, DateTime) and tipo.timezone
    assert isinstance(tabla("alertas").columns["fecha_comunicacion_alerta"].type, Date)  # esa sigue siendo fecha


# --- CHECK aprobados ---------------------------------------------------------------------------------------------


def _checks(nombre_tabla: str) -> dict[str, str]:
    return {
        c.name: str(c.sqltext)
        for c in tabla(nombre_tabla).constraints
        if c.__class__.__name__ == "CheckConstraint" and c.name
    }


@pytest.mark.parametrize(
    ("nombre_tabla", "nombre_check", "contiene"),
    [
        ("diagnosticos", "ck_diagnosticos_categoria_requerida", "'CONFIRMADO', 'OTRA_PATOLOGIA'"),
        ("diagnosticos", "ck_diagnosticos_fecha_con_resultado", "fecha_diagnostico IS NOT NULL"),
        ("auditoria", "ck_auditoria_origen_coherente", "'LIDER'"),
        ("cultivos", "ck_cultivos_tipo_cultivo_valido", "'INVERNADERO', 'CAMPO_ABIERTO'"),
        ("fotografias", "ck_fotografias_es_util_sn", "'S', 'N'"),
        ("alerta_destinatarios", "ck_alerta_destinatarios_tipo_envio_valido", "'ALERTA', 'CORRECCION'"),
    ],
)
def test_check_aprobado(nombre_tabla: str, nombre_check: str, contiene: str) -> None:
    assert contiene in _checks(nombre_tabla)[nombre_check]


def test_no_se_agregaron_checks_que_nadie_aprobo() -> None:
    """estado_alerta, estado_envio y resultado_tecnico se validan en la API, no con CHECK (guía §6.4)."""
    assert not any("estado_alerta" in sql for sql in _checks("alertas").values())
    assert "estado_envio" not in " ".join(_checks("alerta_destinatarios").values())
    assert "resultado_tecnico NOT IN" in " ".join(_checks("diagnosticos").values())  # solo la regla de categoría
