"""Existencia de cada objeto de la guía §6.6 y comportamiento de las funciones."""

import math
import re

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.orm import Session

from app.core import catalogos_api as api
from app.infrastructure.db import procedimientos as sp
from tests.db_fixtures import ROOT
from tests.infrastructure.db.factories import falla_con, id_estado

VISTAS = {
    "vw_casos_detalle",
    "vw_casos_pendientes_revision",
    "vw_diagnosticos_detalle",
    "vw_casos_confirmados_transmisibles",
    "vw_ultima_actividad_caso",
    "vw_auditoria_detalle",
}
FUNCIONES = {"fn_id_estado", "fn_distancia_km"}
PROCEDIMIENTOS = {
    "sp_registrar_evento",
    "sp_registrar_auditoria",
    "sp_registrar_diagnostico",
    "sp_evaluar_alerta",
    "sp_marcar_advertencia_enviada",
    "sp_cerrar_caso",
}
TRIGGERS = {
    ("trg_casos_creacion_evento", "casos"),
    ("trg_casos_estado_evento", "casos"),
    ("trg_diag_evento", "diagnosticos"),
    ("trg_diag_tecnico_activo", "diagnosticos"),
    ("trg_param_transmisible", "parametros_epidemiologicos"),
    ("trg_auditoria_inmutable", "auditoria"),
}


def test_existen_las_seis_vistas(conn: Connection) -> None:
    existentes = set(conn.scalars(text("SELECT viewname FROM pg_views WHERE schemaname = 'public'")))
    assert VISTAS <= existentes


def test_existen_las_funciones_del_avance_5(conn: Connection) -> None:
    existentes = set(
        conn.scalars(
            text("SELECT proname FROM pg_proc WHERE prokind = :tipo AND pronamespace = 'public'::regnamespace"),
            {"tipo": "f"},
        )
    )
    assert FUNCIONES <= existentes


def test_los_sp_son_procedimientos(conn: Connection) -> None:
    """Se invocan con CALL, no con SELECT: son PROCEDURE y no FUNCTION."""
    existentes = set(
        conn.scalars(
            text("SELECT proname FROM pg_proc WHERE prokind = :tipo AND pronamespace = 'public'::regnamespace"),
            {"tipo": "p"},
        )
    )
    assert PROCEDIMIENTOS <= existentes


def test_existen_los_seis_triggers_en_su_tabla(conn: Connection) -> None:
    filas = conn.execute(
        text(
            "SELECT t.tgname, c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE NOT t.tgisinternal"
        )
    ).all()
    assert TRIGGERS <= {(f[0], f[1]) for f in filas}


def test_las_vistas_no_traen_columnas_de_contrasena(conn: Connection) -> None:
    columnas = set(
        conn.scalars(
            text("SELECT column_name FROM information_schema.columns WHERE table_name LIKE 'vw\\_%' ESCAPE '\\'")
        )
    )
    assert not {c for c in columnas if "contrasena" in c}


# --- fn_id_estado ------------------------------------------------------------------------------------------------


@pytest.mark.usefixtures("semillas")
@pytest.mark.parametrize("nombre", api.ESTADOS_CASO.nombres)
def test_fn_id_estado_resuelve_cada_estado(session: Session, nombre: str) -> None:
    resuelto = id_estado(session, nombre)
    assert resuelto.startswith("ES")
    assert session.scalar(text("SELECT nombre_estado FROM estados WHERE id_estado = :i"), {"i": resuelto}) == nombre


def test_los_nombres_de_estado_escritos_en_sql_son_los_del_catalogo() -> None:
    """Los objetos SQL escriben los nombres de los estados tal cual: si el catálogo cambia, esta prueba lo avisa."""
    fuente = (ROOT / "migrations" / "versions" / "0003_objetos_sql.py").read_text(encoding="utf-8")
    usados = set(re.findall(r"fn_id_estado\('([^']+)'\)", fuente))
    usados |= set(re.findall(r"nombre_estado = '([^']+)'", fuente))
    bloque_case = fuente.split("CASE v_nombre", 1)[1].split("END CASE", 1)[0]  # trigger de cambios de estado
    usados |= set(re.findall(r"WHEN '([^']+)' THEN", bloque_case))
    for lista in re.findall(r"nombre_estado IN \(([^)]*)\)", fuente):
        usados |= set(re.findall(r"'([^']+)'", lista))
    assert usados, "no se encontró ningún nombre de estado en la migración"
    assert usados <= set(api.ESTADOS_CASO.nombres), usados - set(api.ESTADOS_CASO.nombres)
    assert {"Caso creado", "En análisis", "Pendiente de revisión", "En revisión", "Registrado", "Cerrado"} <= usados


def test_los_valores_escritos_en_sql_son_los_de_los_catalogos() -> None:
    """Tipos de evento, resultados técnicos y orígenes que validan los procedimientos."""
    fuente = (ROOT / "migrations" / "versions" / "0003_objetos_sql.py").read_text(encoding="utf-8")

    def lista(despues_de: str) -> set[str]:
        bloque = fuente.split(despues_de, 1)[1].split(")", 1)[0]
        return set(re.findall(r"'([A-Z_]+)'", bloque))

    assert lista("IF p_tipo_evento NOT IN (") == set(api.TIPOS_EVENTO.codigos)
    assert lista("IF p_resultado_tecnico NOT IN (") == set(api.RESULTADOS_TECNICOS.codigos)


@pytest.mark.usefixtures("semillas")
def test_fn_id_estado_desconocido(session: Session) -> None:
    falla_con(session, sp.SqlState.NO_ENCONTRADO, lambda: id_estado(session, "Reabierto"))


def test_fn_id_estado_sin_semillas(session: Session) -> None:
    falla_con(session, sp.SqlState.NO_ENCONTRADO, lambda: id_estado(session, "Caso creado"))


# --- fn_distancia_km ---------------------------------------------------------------------------------------------


def _distancia(session: Session, a: str, b: str) -> float | None:
    return session.scalar(
        text(f"SELECT fn_distancia_km(ST_SetSRID(ST_MakePoint({a}), 4326), ST_SetSRID(ST_MakePoint({b}), 4326))")
    )


def test_un_grado_de_longitud_en_el_ecuador(session: Session) -> None:
    assert _distancia(session, "0, 0", "1, 0") == pytest.approx(111.3195, abs=0.01)


def test_un_grado_de_latitud_en_el_ecuador(session: Session) -> None:
    assert _distancia(session, "0, 0", "0, 1") == pytest.approx(110.574, abs=0.01)


def test_distancia_cero_en_el_mismo_punto(session: Session) -> None:
    assert _distancia(session, "-73.17, 5.28", "-73.17, 5.28") == 0


def test_la_distancia_es_simetrica(session: Session) -> None:
    ida = _distancia(session, "-73.17, 5.28", "-73.14, 5.30")
    vuelta = _distancia(session, "-73.14, 5.30", "-73.17, 5.28")
    assert ida == pytest.approx(vuelta)


def test_distancia_en_zetaquira(session: Session) -> None:
    """0,03 grados de latitud cerca de Zetaquirá son unos 3,3 km: queda dentro del radio de 3,5 km de Trips."""
    km = _distancia(session, "-73.17, 5.28", "-73.17, 5.31")
    assert km is not None and 3.2 < km < 3.5 and not math.isnan(km)


def test_distancia_con_un_punto_nulo_es_nula(session: Session) -> None:
    assert session.scalar(text("SELECT fn_distancia_km(NULL, ST_SetSRID(ST_MakePoint(0, 0), 4326))")) is None


# --- fn_enmascarar_telefonos -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("573001234567", "***4567"),
        ("3001234567", "***4567"),
        ("Se envió a 573001234567 y a 3109998877", "Se envió a ***4567 y a ***8877"),
        ("sin números", "sin números"),
        ("CAS-00000241", "CAS-00000241"),  # un id no es un teléfono
        ("1234567890", "1234567890"),  # 10 dígitos que no empiezan por 3
        ("30012345678", "30012345678"),  # 11 dígitos: no es un celular
        ("", ""),
    ],
)
def test_enmascara_telefonos(session: Session, texto: str, esperado: str) -> None:
    assert session.scalar(text("SELECT fn_enmascarar_telefonos(:t)"), {"t": texto}) == esperado


def test_enmascarar_nulo_devuelve_nulo(session: Session) -> None:
    assert session.scalar(text("SELECT fn_enmascarar_telefonos(NULL)")) is None


# --- contexto de la transacción ----------------------------------------------------------------------------------


def test_sin_contexto_el_actor_es_el_sistema(session: Session) -> None:
    assert session.scalar(text("SELECT fn_actor_tipo()")) == "SISTEMA"
    assert session.scalar(text("SELECT fn_actor_id()")) is None
    assert session.scalar(text("SELECT fn_actor_tipo('PRODUCTOR')")) == "PRODUCTOR"


def test_el_contexto_fija_el_actor(session: Session) -> None:
    session.execute(sp.ESTABLECER_ACTOR, {"tipo": "LIDER", "id": "LID-000001"})
    assert session.scalar(text("SELECT fn_actor_tipo()")) == "LIDER"
    assert session.scalar(text("SELECT fn_actor_id()")) == "LID-000001"


def test_el_contexto_dura_solo_la_transaccion(migrated: Engine) -> None:
    """set_config(..., true) es local a la transacción: al confirmarla el actor vuelve a ser el sistema."""
    with migrated.connect() as c:
        c.execute(sp.ESTABLECER_ACTOR, {"tipo": "TECNICO", "id": "TEC-000001"})
        assert c.scalar(text("SELECT fn_actor_tipo()")) == "TECNICO"
        c.commit()
        assert c.scalar(text("SELECT fn_actor_tipo()")) == "SISTEMA"
        assert c.scalar(text("SELECT fn_actor_id()")) is None


def test_el_reloj_simulado(session: Session) -> None:
    antes = session.scalar(text("SELECT fn_ahora()"))
    session.execute(sp.ESTABLECER_AHORA, {"ahora": "2026-01-15 10:00:00+00"})
    simulado = session.scalar(text("SELECT fn_ahora()"))
    assert antes != simulado
    assert simulado.isoformat() == "2026-01-15T10:00:00+00:00"
