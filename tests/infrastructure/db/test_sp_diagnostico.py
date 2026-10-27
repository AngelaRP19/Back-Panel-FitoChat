"""sp_registrar_diagnostico: En revisión + técnico de la asignación vigente → Registrado, con evento y auditoría."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.infrastructure.db import procedimientos as sp
from app.infrastructure.db.models import CategoriaFitosanitaria, Diagnostico
from tests.infrastructure.db.factories import (
    asignar,
    caso_en,
    caso_en_revision,
    crear_tecnico,
    escenario,
    estado_de,
    eventos_de,
    falla_con,
    llamar,
)

pytestmark = pytest.mark.usefixtures("semillas")

FECHA = datetime(2026, 10, 27, 15, 4, tzinfo=UTC)


def trips(session: Session) -> str:
    consulta = select(CategoriaFitosanitaria).where(CategoriaFitosanitaria.nombre_categoria == "Trips")
    return session.scalars(consulta).one().id_categoria


def diagnosticar(session: Session, id_caso: str, id_tecnico: str, resultado: str, **extra: object) -> None:
    llamar(session, sp.REGISTRAR_DIAGNOSTICO, **sp.parametros_diagnostico(id_caso, id_tecnico, resultado, **extra))  # type: ignore[arg-type]


def diagnostico_de(session: Session, id_caso: str) -> dict[str, object]:
    return dict(session.execute(text("SELECT * FROM diagnosticos WHERE id_caso = :c"), {"c": id_caso}).mappings().one())


def auditorias(session: Session) -> list[dict[str, object]]:
    filas = session.execute(text("SELECT * FROM auditoria ORDER BY id_auditoria")).mappings().all()
    return [dict(f) for f in filas]


# --- camino feliz ------------------------------------------------------------------------------------------------


def test_el_tecnico_asignado_registra_su_diagnostico(session: Session) -> None:
    tecnico = crear_tecnico(session)
    caso = caso_en_revision(session, escenario(session), tecnico)
    diagnosticar(
        session, caso.id_caso, tecnico.id_tecnico, "CONFIRMADO", id_categoria=trips(session),
        descripcion="Daño en hojas jóvenes", fecha=FECHA,
    )  # fmt: skip
    d = diagnostico_de(session, caso.id_caso)
    assert (d["resultado_tecnico"], d["id_categoria_confirmada"]) == ("CONFIRMADO", trips(session))
    assert (d["descripcion_diagnostico"], d["fecha_diagnostico"]) == ("Daño en hojas jóvenes", FECHA)
    assert estado_de(session, caso.id_caso) == "Registrado"


def test_completa_la_fila_vacia_sin_crear_otra(session: Session) -> None:
    tecnico = crear_tecnico(session)
    caso = caso_en_revision(session, escenario(session), tecnico)
    antes = diagnostico_de(session, caso.id_caso)["id_diagnostico"]
    diagnosticar(session, caso.id_caso, tecnico.id_tecnico, "NO_IDENTIFICADO")
    assert diagnostico_de(session, caso.id_caso)["id_diagnostico"] == antes
    assert session.scalar(text("SELECT count(*) FROM diagnosticos WHERE id_caso = :c"), {"c": caso.id_caso}) == 1


def test_crea_la_fila_si_faltaba(session: Session) -> None:
    tecnico = crear_tecnico(session)
    caso = caso_en(session, escenario(session), "En revisión")
    asignar(session, caso, tecnico)  # sin fila vacía de diagnóstico
    diagnosticar(session, caso.id_caso, tecnico.id_tecnico, "NO_IDENTIFICADO")
    assert diagnostico_de(session, caso.id_caso)["resultado_tecnico"] == "NO_IDENTIFICADO"


def test_anota_el_evento_con_el_tecnico_como_actor(session: Session) -> None:
    tecnico = crear_tecnico(session)
    caso = caso_en_revision(session, escenario(session), tecnico)
    diagnosticar(session, caso.id_caso, tecnico.id_tecnico, "NO_IDENTIFICADO")
    eventos = eventos_de(session, caso.id_caso)
    assert eventos.count(("DIAGNOSTICO_REGISTRADO", "TECNICO", tecnico.id_tecnico)) == 1
    assert [e[0] for e in eventos].count("DIAGNOSTICO_REGISTRADO") == 1  # Registrado no duplica el evento


def test_el_evento_y_la_auditoria_llevan_la_fecha_del_diagnostico(session: Session) -> None:
    tecnico = crear_tecnico(session)
    caso = caso_en_revision(session, escenario(session), tecnico)
    diagnosticar(session, caso.id_caso, tecnico.id_tecnico, "NO_IDENTIFICADO", fecha=FECHA)
    fecha_evento = session.scalar(
        text("SELECT fecha_evento FROM eventos WHERE id_caso = :c AND tipo_evento = 'DIAGNOSTICO_REGISTRADO'"),
        {"c": caso.id_caso},
    )
    assert fecha_evento == FECHA
    assert auditorias(session)[-1]["fecha_auditoria"] == FECHA


def test_registra_la_auditoria_del_tecnico(session: Session) -> None:
    tecnico = crear_tecnico(session)
    caso = caso_en_revision(session, escenario(session), tecnico)
    diagnosticar(session, caso.id_caso, tecnico.id_tecnico, "NO_IDENTIFICADO")
    a = auditorias(session)[-1]
    assert (a["origen"], a["id_tecnico"], a["id_lider_tecnico"]) == ("TECNICO", tecnico.id_tecnico, None)
    assert (a["accion"], a["entidad_afectada"], a["resultado"], a["valor_nuevo"]) == (
        "DIAGNOSTICAR",
        "diagnosticos",
        "EXITO",
        "NO_IDENTIFICADO",
    )
    assert caso.id_caso in str(a["descripcion"])


@pytest.mark.parametrize(
    ("resultado", "extra"),
    [
        ("CONFIRMADO", {"categoria": True}),
        ("OTRA_PATOLOGIA", {"categoria": True}),
        ("NO_IDENTIFICADO", {}),
        ("REQUIERE_VISITA", {"motivo_visita": "Revisar el lote en campo"}),
    ],
)
def test_cada_resultado_valido(session: Session, resultado: str, extra: dict[str, object]) -> None:
    tecnico = crear_tecnico(session)
    caso = caso_en_revision(session, escenario(session), tecnico)
    kwargs = {k: v for k, v in extra.items() if k != "categoria"}
    if extra.get("categoria"):
        kwargs["id_categoria"] = trips(session)
    diagnosticar(session, caso.id_caso, tecnico.id_tecnico, resultado, **kwargs)
    assert diagnostico_de(session, caso.id_caso)["resultado_tecnico"] == resultado
    assert estado_de(session, caso.id_caso) == "Registrado"


# --- estado --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "estado", ["Caso creado", "En análisis", "Pendiente de revisión", "Registrado", "Cerrado", "Descartado"]
)
def test_solo_se_diagnostica_un_caso_en_revision(session: Session, estado: str) -> None:
    tecnico = crear_tecnico(session)
    caso = caso_en(session, escenario(session), estado)
    asignar(session, caso, tecnico)
    falla_con(
        session,
        sp.SqlState.TRANSICION_INVALIDA,
        lambda: diagnosticar(session, caso.id_caso, tecnico.id_tecnico, "NO_IDENTIFICADO"),
    )
    assert estado_de(session, caso.id_caso) == estado


def test_un_segundo_diagnostico_se_rechaza(session: Session) -> None:
    tecnico = crear_tecnico(session)
    caso = caso_en_revision(session, escenario(session), tecnico)
    diagnosticar(session, caso.id_caso, tecnico.id_tecnico, "NO_IDENTIFICADO")
    falla_con(
        session,
        sp.SqlState.TRANSICION_INVALIDA,
        lambda: diagnosticar(session, caso.id_caso, tecnico.id_tecnico, "REQUIERE_VISITA", motivo_visita="x"),
    )
    assert diagnostico_de(session, caso.id_caso)["resultado_tecnico"] == "NO_IDENTIFICADO"  # no se sobrescribió


def test_un_caso_inexistente(session: Session) -> None:
    tecnico = crear_tecnico(session)
    falla_con(
        session,
        sp.SqlState.NO_ENCONTRADO,
        lambda: diagnosticar(session, "CAS-99999999", tecnico.id_tecnico, "NO_IDENTIFICADO"),
    )


# --- quién diagnostica -------------------------------------------------------------------------------------------


def test_otro_tecnico_no_puede_diagnosticar(session: Session) -> None:
    asignado, intruso = crear_tecnico(session, 1), crear_tecnico(session, 2)
    caso = caso_en_revision(session, escenario(session), asignado)
    falla_con(
        session,
        sp.SqlState.NO_ES_EL_TECNICO_ASIGNADO,
        lambda: diagnosticar(session, caso.id_caso, intruso.id_tecnico, "NO_IDENTIFICADO"),
    )
    assert estado_de(session, caso.id_caso) == "En revisión"


def test_el_tecnico_de_una_asignacion_ya_cerrada_no_puede_diagnosticar(session: Session) -> None:
    anterior, actual = crear_tecnico(session, 1), crear_tecnico(session, 2)
    caso = caso_en(session, escenario(session), "En revisión")
    asignar(session, caso, anterior, vigente=False)
    asignar(session, caso, actual)
    session.add(Diagnostico(id_caso=caso.id_caso))
    session.flush()
    falla_con(
        session,
        sp.SqlState.NO_ES_EL_TECNICO_ASIGNADO,
        lambda: diagnosticar(session, caso.id_caso, anterior.id_tecnico, "NO_IDENTIFICADO"),
    )
    diagnosticar(session, caso.id_caso, actual.id_tecnico, "NO_IDENTIFICADO")  # el vigente sí puede
    assert estado_de(session, caso.id_caso) == "Registrado"


def test_sin_asignacion_vigente_nadie_puede_diagnosticar(session: Session) -> None:
    tecnico = crear_tecnico(session)
    caso = caso_en(session, escenario(session), "En revisión")
    falla_con(
        session,
        sp.SqlState.NO_ES_EL_TECNICO_ASIGNADO,
        lambda: diagnosticar(session, caso.id_caso, tecnico.id_tecnico, "NO_IDENTIFICADO"),
    )


def test_un_tecnico_deshabilitado_no_puede_diagnosticar(session: Session) -> None:
    tecnico = crear_tecnico(session)
    caso = caso_en_revision(session, escenario(session), tecnico)
    tecnico.estado_cuenta_tecnico = "DESHABILITADO"
    session.flush()
    falla_con(
        session,
        sp.SqlState.NO_ES_EL_TECNICO_ASIGNADO,  # lo rechaza trg_diag_tecnico_activo
        lambda: diagnosticar(session, caso.id_caso, tecnico.id_tecnico, "NO_IDENTIFICADO"),
    )
    assert estado_de(session, caso.id_caso) == "En revisión"


def test_un_lider_no_puede_diagnosticar_con_su_id(session: Session) -> None:
    """D-02: el diagnóstico es de un técnico; un id de líder nunca coincide con la asignación vigente."""
    tecnico = crear_tecnico(session)
    caso = caso_en_revision(session, escenario(session), tecnico)
    falla_con(
        session,
        sp.SqlState.NO_ES_EL_TECNICO_ASIGNADO,
        lambda: diagnosticar(session, caso.id_caso, "LID-000001", "NO_IDENTIFICADO"),
    )


# --- datos -------------------------------------------------------------------------------------------------------


def test_resultado_desconocido(session: Session) -> None:
    tecnico = crear_tecnico(session)
    caso = caso_en_revision(session, escenario(session), tecnico)
    falla_con(
        session,
        sp.SqlState.DATOS_INVALIDOS,
        lambda: diagnosticar(session, caso.id_caso, tecnico.id_tecnico, "SANO"),
    )


@pytest.mark.parametrize("resultado", ["CONFIRMADO", "OTRA_PATOLOGIA"])
def test_confirmado_y_otra_patologia_exigen_categoria(session: Session, resultado: str) -> None:
    tecnico = crear_tecnico(session)
    caso = caso_en_revision(session, escenario(session), tecnico)
    falla_con(
        session,
        sp.SqlState.DATOS_INVALIDOS,
        lambda: diagnosticar(session, caso.id_caso, tecnico.id_tecnico, resultado),
    )


@pytest.mark.parametrize("motivo", [None, "", "   "])
def test_requiere_visita_exige_el_motivo(session: Session, motivo: str | None) -> None:
    tecnico = crear_tecnico(session)
    caso = caso_en_revision(session, escenario(session), tecnico)
    falla_con(
        session,
        sp.SqlState.DATOS_INVALIDOS,
        lambda: diagnosticar(session, caso.id_caso, tecnico.id_tecnico, "REQUIERE_VISITA", motivo_visita=motivo),
    )


def test_una_categoria_inexistente_viola_la_clave_foranea(session: Session) -> None:
    from sqlalchemy.exc import IntegrityError

    tecnico = crear_tecnico(session)
    caso = caso_en_revision(session, escenario(session), tecnico)
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            diagnosticar(session, caso.id_caso, tecnico.id_tecnico, "CONFIRMADO", id_categoria="CF999")


def test_un_fallo_no_deja_cambios_a_medias(session: Session) -> None:
    """Todo o nada: si falla, el caso sigue En revisión, sin resultado, sin evento nuevo y sin auditoría."""
    tecnico = crear_tecnico(session)
    caso = caso_en_revision(session, escenario(session), tecnico)
    eventos = eventos_de(session, caso.id_caso)
    auditoria_antes = len(auditorias(session))
    falla_con(
        session,
        sp.SqlState.DATOS_INVALIDOS,
        lambda: diagnosticar(session, caso.id_caso, tecnico.id_tecnico, "CONFIRMADO"),
    )
    assert estado_de(session, caso.id_caso) == "En revisión"
    assert diagnostico_de(session, caso.id_caso)["resultado_tecnico"] is None
    assert eventos_de(session, caso.id_caso) == eventos and len(auditorias(session)) == auditoria_antes


def test_sin_fecha_usa_la_hora_actual(session: Session) -> None:
    tecnico = crear_tecnico(session)
    caso = caso_en_revision(session, escenario(session), tecnico)
    diagnosticar(session, caso.id_caso, tecnico.id_tecnico, "NO_IDENTIFICADO")
    fecha = diagnostico_de(session, caso.id_caso)["fecha_diagnostico"]
    assert isinstance(fecha, datetime) and abs((datetime.now(UTC) - fecha).total_seconds()) < 60
