"""sp_registrar_evento y sp_registrar_auditoria."""

import re
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import catalogos_api as api
from app.infrastructure.db import procedimientos as sp
from tests.infrastructure.db.factories import (
    caso_en,
    crear_lider,
    crear_tecnico,
    escenario,
    eventos_de,
    falla_con,
    llamar,
    viola,
)

pytestmark = pytest.mark.usefixtures("semillas")

FECHA = datetime(2026, 5, 4, 12, 30, tzinfo=UTC)


def _evento(session: Session, id_caso: str, tipo: str = "TECNICO_REASIGNADO", **extra: object) -> None:
    parametros: dict[str, object] = {
        "id_caso": id_caso,
        "tipo_evento": tipo,
        "descripcion": "Se reasignó el caso",
        "actor_tipo": "LIDER",
        "actor_id": "LID-000001",
        "fecha": None,
    }
    parametros.update(extra)
    llamar(session, sp.REGISTRAR_EVENTO, **parametros)


def _auditoria(session: Session, **extra: object) -> None:
    parametros: dict[str, object] = {
        "origen": "LIDER",
        "id_actor": None,
        "accion": "ASIGNAR",
        "entidad": "casos",
        "descripcion": "Caso asignado",
        "valor_anterior": None,
        "valor_nuevo": None,
        "resultado": "EXITO",
        "fecha": None,
    }
    parametros.update(extra)
    llamar(session, sp.REGISTRAR_AUDITORIA, **parametros)


def _ultima_auditoria(session: Session) -> dict[str, object]:
    return dict(session.execute(text("SELECT * FROM auditoria ORDER BY id_auditoria DESC LIMIT 1")).mappings().one())


# --- sp_registrar_evento -----------------------------------------------------------------------------------------


def test_registra_un_evento_con_su_actor(session: Session) -> None:
    caso = caso_en(session, escenario(session), "Pendiente de revisión")
    _evento(session, caso.id_caso, fecha=FECHA)
    fila = (
        session.execute(
            text("SELECT * FROM eventos WHERE id_caso = :c AND tipo_evento = 'TECNICO_REASIGNADO'"), {"c": caso.id_caso}
        )
        .mappings()
        .one()
    )
    assert re.fullmatch(r"EVE-\d{8}", fila["id_evento"])
    assert (fila["actor_tipo"], fila["actor_id"], fila["fecha_evento"]) == ("LIDER", "LID-000001", FECHA)
    assert fila["descripcion_evento"] == "Se reasignó el caso"


def test_el_actor_es_opcional(session: Session) -> None:
    caso = caso_en(session, escenario(session), "Pendiente de revisión")
    _evento(session, caso.id_caso, actor_tipo=None, actor_id=None)
    assert eventos_de(session, caso.id_caso)[-1] == ("TECNICO_REASIGNADO", None, None)


def test_sin_fecha_usa_el_reloj_de_la_transaccion(session: Session) -> None:
    caso = caso_en(session, escenario(session), "Pendiente de revisión")
    llamar(session, sp.ESTABLECER_AHORA, ahora="2026-02-02 02:02:00+00")
    _evento(session, caso.id_caso)
    fecha = session.scalar(
        text("SELECT fecha_evento FROM eventos WHERE tipo_evento = 'TECNICO_REASIGNADO' AND id_caso = :c"),
        {"c": caso.id_caso},
    )
    assert fecha == datetime(2026, 2, 2, 2, 2, tzinfo=UTC)


@pytest.mark.parametrize("tipo", api.TIPOS_EVENTO.codigos)
def test_acepta_cada_tipo_del_catalogo(session: Session, tipo: str) -> None:
    caso = caso_en(session, escenario(session), "Pendiente de revisión")
    _evento(session, caso.id_caso, tipo)
    assert eventos_de(session, caso.id_caso)[-1][0] == tipo


def test_rechaza_un_tipo_de_evento_desconocido(session: Session) -> None:
    caso = caso_en(session, escenario(session), "Pendiente de revisión")
    falla_con(session, sp.SqlState.DATOS_INVALIDOS, lambda: _evento(session, caso.id_caso, "CASO_REABIERTO"))


@pytest.mark.parametrize("actor", ["LIDER", "TECNICO", "PRODUCTOR", "SISTEMA"])
def test_acepta_cada_tipo_de_actor(session: Session, actor: str) -> None:
    caso = caso_en(session, escenario(session), "Pendiente de revisión")
    _evento(session, caso.id_caso, actor_tipo=actor)
    assert eventos_de(session, caso.id_caso)[-1][1] == actor


def test_rechaza_un_tipo_de_actor_desconocido(session: Session) -> None:
    caso = caso_en(session, escenario(session), "Pendiente de revisión")
    falla_con(session, sp.SqlState.DATOS_INVALIDOS, lambda: _evento(session, caso.id_caso, actor_tipo="ADMIN"))


def test_el_caso_debe_existir(session: Session) -> None:
    viola(session, "fk_eve_caso", lambda: _evento(session, "CAS-99999999"))


def test_los_eventos_quedan_en_orden_de_registro(session: Session) -> None:
    caso = caso_en(session, escenario(session), "Pendiente de revisión")
    for tipo in ("TECNICO_ASIGNADO", "TECNICO_REASIGNADO", "TECNICO_REASIGNADO"):
        _evento(session, caso.id_caso, tipo)
    assert [e[0] for e in eventos_de(session, caso.id_caso)][-3:] == [
        "TECNICO_ASIGNADO",
        "TECNICO_REASIGNADO",
        "TECNICO_REASIGNADO",
    ]


# --- sp_registrar_auditoria --------------------------------------------------------------------------------------


def test_auditoria_de_un_lider(session: Session) -> None:
    lider = crear_lider(session)
    _auditoria(session, origen="LIDER", id_actor=lider.id_lider_tecnico, fecha=FECHA)
    fila = _ultima_auditoria(session)
    assert re.fullmatch(r"AUD-\d{8}", str(fila["id_auditoria"]))
    assert (fila["origen"], fila["id_lider_tecnico"], fila["id_tecnico"]) == ("LIDER", lider.id_lider_tecnico, None)
    assert (fila["accion"], fila["entidad_afectada"], fila["resultado"], fila["fecha_auditoria"]) == (
        "ASIGNAR",
        "casos",
        "EXITO",
        FECHA,
    )


def test_auditoria_de_un_tecnico(session: Session) -> None:
    tecnico = crear_tecnico(session)
    _auditoria(session, origen="TECNICO", id_actor=tecnico.id_tecnico)
    fila = _ultima_auditoria(session)
    assert (fila["origen"], fila["id_tecnico"], fila["id_lider_tecnico"]) == ("TECNICO", tecnico.id_tecnico, None)


def test_auditoria_del_sistema_ignora_el_id_de_actor(session: Session) -> None:
    _auditoria(session, origen="SISTEMA", id_actor="LID-000001")
    fila = _ultima_auditoria(session)
    assert (fila["origen"], fila["id_lider_tecnico"], fila["id_tecnico"]) == ("SISTEMA", None, None)


def test_rechaza_un_origen_desconocido(session: Session) -> None:
    falla_con(session, sp.SqlState.DATOS_INVALIDOS, lambda: _auditoria(session, origen="ADMIN"))


def test_un_lider_inexistente_viola_la_clave_foranea(session: Session) -> None:
    viola(session, "fk_aud_lider", lambda: _auditoria(session, origen="LIDER", id_actor="LID-999999"))


def test_un_tecnico_inexistente_viola_la_clave_foranea(session: Session) -> None:
    viola(session, "fk_aud_tecnico", lambda: _auditoria(session, origen="TECNICO", id_actor="TEC-999999"))


def test_un_lider_sin_id_viola_la_regla_del_origen(session: Session) -> None:
    """El CHECK de la tabla (D-09) sigue protegiendo aunque se llame al procedimiento mal."""
    viola(session, "ck_auditoria_origen_coherente", lambda: _auditoria(session, origen="LIDER", id_actor=None))


def test_la_auditoria_no_guarda_telefonos_completos(session: Session) -> None:
    _auditoria(
        session,
        origen="SISTEMA",
        descripcion="Alerta enviada a 573001234567",
        valor_anterior="3109998877",
        valor_nuevo="Contacto 573201112233 y 3001234567",
    )
    fila = _ultima_auditoria(session)
    assert fila["descripcion"] == "Alerta enviada a ***4567"
    assert fila["valor_anterior"] == "***8877"
    assert fila["valor_nuevo"] == "Contacto ***2233 y ***4567"
    assert "5730012" not in " ".join(str(v) for v in fila.values())


def test_los_valores_pueden_ser_nulos(session: Session) -> None:
    _auditoria(session, origen="SISTEMA", valor_anterior=None, valor_nuevo=None)
    fila = _ultima_auditoria(session)
    assert fila["valor_anterior"] is None and fila["valor_nuevo"] is None


def test_sin_fecha_usa_el_reloj_simulado(session: Session) -> None:
    llamar(session, sp.ESTABLECER_AHORA, ahora="2026-06-06 06:06:00+00")
    _auditoria(session, origen="SISTEMA")
    assert _ultima_auditoria(session)["fecha_auditoria"] == datetime(2026, 6, 6, 6, 6, tzinfo=UTC)


def test_el_id_de_auditoria_no_se_repite(session: Session) -> None:
    for _ in range(3):
        _auditoria(session, origen="SISTEMA")
    ids = session.scalars(text("SELECT id_auditoria FROM auditoria")).all()
    assert len(ids) == len(set(ids)) >= 3


def test_error_de_integridad_no_es_un_error_de_negocio(session: Session) -> None:
    with pytest.raises(IntegrityError) as exc:
        with session.begin_nested():
            _auditoria(session, origen="LIDER", id_actor="LID-999999")
    assert sp.error_de_negocio(exc.value) is None  # es un 23503 estándar, no un AN***
