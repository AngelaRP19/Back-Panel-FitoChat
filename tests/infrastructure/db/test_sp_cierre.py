"""sp_cerrar_caso y sp_marcar_advertencia_enviada."""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.infrastructure.db import procedimientos as sp
from tests.infrastructure.db.factories import (
    caso_en,
    crear_lider,
    crear_tecnico,
    escenario,
    estado_de,
    eventos_de,
    falla_con,
    llamar,
)

pytestmark = pytest.mark.usefixtures("semillas")

CIERRE = datetime(2026, 10, 27, 15, 4, tzinfo=UTC)


def cerrar(
    session: Session, id_caso: str, actor_tipo: str, actor_id: str, *, meses: int = 6, fecha: object = CIERRE
) -> None:
    llamar(session, sp.CERRAR_CASO, id_caso=id_caso, actor_tipo=actor_tipo, actor_id=actor_id, meses=meses, fecha=fecha)


def fila(session: Session, id_caso: str) -> dict[str, object]:
    return dict(session.execute(text("SELECT * FROM casos WHERE id_caso = :c"), {"c": id_caso}).mappings().one())


# --- sp_cerrar_caso ----------------------------------------------------------------------------------------------


def test_un_tecnico_cierra_un_caso_registrado(session: Session) -> None:
    tecnico = crear_tecnico(session)
    caso = caso_en(session, escenario(session), "Registrado")
    cerrar(session, caso.id_caso, "TECNICO", tecnico.id_tecnico)
    assert estado_de(session, caso.id_caso) == "Cerrado"
    f = fila(session, caso.id_caso)
    assert f["fecha_cierre"] == CIERRE and f["vence_en"] == date(2027, 4, 27)  # cierre + 6 meses


def test_un_lider_tambien_puede_cerrar(session: Session) -> None:
    lider = crear_lider(session)
    caso = caso_en(session, escenario(session), "Registrado")
    cerrar(session, caso.id_caso, "LIDER", lider.id_lider_tecnico)
    assert estado_de(session, caso.id_caso) == "Cerrado"


def test_anota_el_evento_con_el_actor(session: Session) -> None:
    lider = crear_lider(session)
    caso = caso_en(session, escenario(session), "Registrado")
    cerrar(session, caso.id_caso, "LIDER", lider.id_lider_tecnico)
    assert eventos_de(session, caso.id_caso)[-1] == ("CASO_CERRADO", "LIDER", lider.id_lider_tecnico)
    fecha = session.scalar(
        text("SELECT fecha_evento FROM eventos WHERE id_caso = :c AND tipo_evento = 'CASO_CERRADO'"),
        {"c": caso.id_caso},
    )
    assert fecha == CIERRE


def test_registra_la_auditoria_de_quien_cierra(session: Session) -> None:
    tecnico = crear_tecnico(session)
    caso = caso_en(session, escenario(session), "Registrado")
    cerrar(session, caso.id_caso, "TECNICO", tecnico.id_tecnico)
    a = dict(session.execute(text("SELECT * FROM auditoria ORDER BY id_auditoria DESC LIMIT 1")).mappings().one())
    assert (a["origen"], a["id_tecnico"], a["accion"], a["entidad_afectada"], a["resultado"]) == (
        "TECNICO",
        tecnico.id_tecnico,
        "CERRAR_CASO",
        "casos",
        "EXITO",
    )
    assert caso.id_caso in str(a["descripcion"]) and "2027-04-27" in str(a["descripcion"])


@pytest.mark.parametrize(
    ("cierre", "meses", "vence"),
    [
        (datetime(2026, 10, 31, 12, 0, tzinfo=UTC), 6, date(2027, 4, 30)),  # abril no tiene día 31: queda el 30
        (datetime(2026, 8, 31, 12, 0, tzinfo=UTC), 6, date(2027, 2, 28)),
        (datetime(2024, 8, 31, 12, 0, tzinfo=UTC), 6, date(2025, 2, 28)),
        (datetime(2025, 8, 29, 12, 0, tzinfo=UTC), 6, date(2026, 2, 28)),
        (datetime(2026, 1, 15, 12, 0, tzinfo=UTC), 6, date(2026, 7, 15)),
        (datetime(2026, 1, 15, 12, 0, tzinfo=UTC), 12, date(2027, 1, 15)),
        (datetime(2026, 1, 15, 12, 0, tzinfo=UTC), 1, date(2026, 2, 15)),
    ],
)
def test_el_vencimiento_es_el_cierre_mas_los_meses_configurados(
    session: Session, cierre: datetime, meses: int, vence: date
) -> None:
    caso = caso_en(session, escenario(session), "Registrado")
    cerrar(session, caso.id_caso, "TECNICO", crear_tecnico(session).id_tecnico, meses=meses, fecha=cierre)
    assert fila(session, caso.id_caso)["vence_en"] == vence


def test_el_vencimiento_se_cuenta_con_la_fecha_de_colombia(session: Session) -> None:
    """2026-08-01 02:00 UTC es el 31 de julio a las 21:00 en Colombia: el cierre cuenta como 31 de julio."""
    caso = caso_en(session, escenario(session), "Registrado")
    cierre = datetime(2026, 8, 1, 2, 0, tzinfo=UTC)
    cerrar(session, caso.id_caso, "TECNICO", crear_tecnico(session).id_tecnico, fecha=cierre)
    assert fila(session, caso.id_caso)["vence_en"] == date(2027, 1, 31)  # y no 2027-02-01


@pytest.mark.parametrize(
    "estado", ["Caso creado", "En análisis", "Pendiente de revisión", "En revisión", "Cerrado", "Descartado"]
)
def test_solo_se_cierra_un_caso_registrado(session: Session, estado: str) -> None:
    caso = caso_en(session, escenario(session), estado)
    falla_con(
        session,
        sp.SqlState.TRANSICION_INVALIDA,
        lambda: cerrar(session, caso.id_caso, "TECNICO", crear_tecnico(session).id_tecnico),
    )
    assert estado_de(session, caso.id_caso) == estado
    assert fila(session, caso.id_caso)["fecha_cierre"] is None


def test_cerrar_dos_veces_se_rechaza_y_no_cambia_el_vencimiento(session: Session) -> None:
    tecnico = crear_tecnico(session)
    caso = caso_en(session, escenario(session), "Registrado")
    cerrar(session, caso.id_caso, "TECNICO", tecnico.id_tecnico)
    vence = fila(session, caso.id_caso)["vence_en"]
    falla_con(
        session,
        sp.SqlState.TRANSICION_INVALIDA,
        lambda: cerrar(session, caso.id_caso, "TECNICO", tecnico.id_tecnico, fecha=datetime(2027, 1, 1, tzinfo=UTC)),
    )
    assert fila(session, caso.id_caso)["vence_en"] == vence


def test_un_caso_cerrado_no_se_reabre_con_estos_procedimientos(session: Session) -> None:
    """Los casos no se reabren: el diagnóstico tampoco acepta un caso Cerrado."""
    tecnico = crear_tecnico(session)
    caso = caso_en(session, escenario(session), "Cerrado")
    falla_con(
        session,
        sp.SqlState.TRANSICION_INVALIDA,
        lambda: llamar(
            session,
            sp.REGISTRAR_DIAGNOSTICO,
            **sp.parametros_diagnostico(caso.id_caso, tecnico.id_tecnico, "NO_IDENTIFICADO"),
        ),
    )


def test_un_caso_inexistente(session: Session) -> None:
    falla_con(
        session,
        sp.SqlState.NO_ENCONTRADO,
        lambda: cerrar(session, "CAS-99999999", "TECNICO", crear_tecnico(session).id_tecnico),
    )


@pytest.mark.parametrize("actor", ["SISTEMA", "PRODUCTOR", "ADMIN", ""])
def test_solo_un_lider_o_un_tecnico_cierra(session: Session, actor: str) -> None:
    caso = caso_en(session, escenario(session), "Registrado")
    falla_con(session, sp.SqlState.DATOS_INVALIDOS, lambda: cerrar(session, caso.id_caso, actor, "X-1"))
    assert estado_de(session, caso.id_caso) == "Registrado"


def test_un_actor_inexistente_no_deja_el_caso_cerrado(session: Session) -> None:
    from sqlalchemy.exc import IntegrityError

    caso = caso_en(session, escenario(session), "Registrado")
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            cerrar(session, caso.id_caso, "TECNICO", "TEC-999999")  # la auditoría exige un técnico real
    assert estado_de(session, caso.id_caso) == "Registrado"  # se revirtió también el cambio de estado


def test_sin_fecha_usa_la_hora_actual_y_6_meses_por_defecto(session: Session) -> None:
    caso = caso_en(session, escenario(session), "Registrado")
    llamar(
        session,
        sp.CERRAR_CASO,
        id_caso=caso.id_caso,
        actor_tipo="TECNICO",
        actor_id=crear_tecnico(session).id_tecnico,
        meses=6,
        fecha=None,
    )
    f = fila(session, caso.id_caso)
    assert isinstance(f["fecha_cierre"], datetime) and abs((datetime.now(UTC) - f["fecha_cierre"]).total_seconds()) < 60
    assert isinstance(f["vence_en"], date) and f["vence_en"] > date.today()


# --- sp_marcar_advertencia_enviada -------------------------------------------------------------------------------


def advertir(session: Session, id_caso: str) -> None:
    llamar(session, sp.MARCAR_ADVERTENCIA_ENVIADA, id_caso=id_caso)


@pytest.mark.parametrize("estado", ["Caso creado", "En análisis"])
def test_marca_la_advertencia_de_los_casos_del_chatbot(session: Session, estado: str) -> None:
    caso = caso_en(session, escenario(session), estado)
    assert fila(session, caso.id_caso)["advertencia_inactividad_enviada"] == "N"
    advertir(session, caso.id_caso)
    assert fila(session, caso.id_caso)["advertencia_inactividad_enviada"] == "S"


def test_marcar_dos_veces_no_falla(session: Session) -> None:
    caso = caso_en(session, escenario(session), "En análisis")
    advertir(session, caso.id_caso)
    advertir(session, caso.id_caso)  # el job puede reintentar
    assert fila(session, caso.id_caso)["advertencia_inactividad_enviada"] == "S"


@pytest.mark.parametrize("estado", ["Pendiente de revisión", "En revisión", "Registrado", "Cerrado", "Descartado"])
def test_no_se_advierte_a_un_caso_que_ya_no_maneja_el_chatbot(session: Session, estado: str) -> None:
    caso = caso_en(session, escenario(session), estado)
    falla_con(session, sp.SqlState.TRANSICION_INVALIDA, lambda: advertir(session, caso.id_caso))
    assert fila(session, caso.id_caso)["advertencia_inactividad_enviada"] == "N"


def test_advertir_un_caso_inexistente(session: Session) -> None:
    falla_con(session, sp.SqlState.NO_ENCONTRADO, lambda: advertir(session, "CAS-99999999"))


def test_la_advertencia_no_cambia_el_estado_ni_la_actividad(session: Session) -> None:
    caso = caso_en(session, escenario(session), "En análisis")
    antes = fila(session, caso.id_caso)["ultima_actividad"]
    advertir(session, caso.id_caso)
    assert estado_de(session, caso.id_caso) == "En análisis"
    assert fila(session, caso.id_caso)["ultima_actividad"] == antes
