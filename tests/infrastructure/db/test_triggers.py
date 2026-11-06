"""Los seis triggers del Avance 5 (guía §6.6), uno por uno."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.infrastructure.db import procedimientos as sp
from app.infrastructure.db.models import Auditoria, Diagnostico
from tests.infrastructure.db.factories import (
    asignar,
    caso_en,
    caso_en_revision,
    crear_categoria,
    crear_lider,
    crear_parametro,
    crear_tecnico,
    escenario,
    eventos_de,
    falla_con,
    id_estado,
    llamar,
)

pytestmark = pytest.mark.usefixtures("semillas")


def _cambiar_estado(session: Session, id_caso: str, nombre: str) -> None:
    session.execute(
        text("UPDATE casos SET id_estado = :e WHERE id_caso = :c"), {"e": id_estado(session, nombre), "c": id_caso}
    )


# --- trg_casos_creacion_evento -----------------------------------------------------------------------------------


def test_crear_un_caso_anota_caso_creado_con_el_productor_como_actor(session: Session) -> None:
    datos = escenario(session)
    caso = caso_en(session, datos, "Caso creado")
    assert eventos_de(session, caso.id_caso) == [("CASO_CREADO", "PRODUCTOR", datos.productor.id_productor)]


def test_el_evento_de_creacion_usa_la_fecha_de_creacion_del_caso(session: Session) -> None:
    datos = escenario(session)
    caso = caso_en(session, datos, "Caso creado")
    fecha = session.scalar(text("SELECT fecha_evento FROM eventos WHERE id_caso = :c"), {"c": caso.id_caso})
    assert fecha == caso.fecha_creacion_caso


def test_el_actor_de_la_transaccion_manda_sobre_el_productor(session: Session) -> None:
    datos = escenario(session)
    llamar(session, sp.ESTABLECER_ACTOR, tipo="SISTEMA", id="")
    caso = caso_en(session, datos, "Caso creado")
    assert eventos_de(session, caso.id_caso) == [("CASO_CREADO", "SISTEMA", None)]


def test_cada_caso_tiene_un_solo_evento_de_creacion(session: Session) -> None:
    datos = escenario(session)
    casos = [caso_en(session, datos, "Caso creado") for _ in range(3)]
    for caso in casos:
        assert [e[0] for e in eventos_de(session, caso.id_caso)] == ["CASO_CREADO"]


# --- trg_casos_estado_evento -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("destino", "evento"),
    [
        ("En análisis", "ANALISIS_CHATBOT"),
        ("Pendiente de revisión", "ENVIADO_A_REVISION"),
        ("En revisión", "TECNICO_ASIGNADO"),
        ("Cerrado", "CASO_CERRADO"),
        ("Descartado", "CASO_DESCARTADO"),
    ],
)
def test_cada_cambio_de_estado_anota_su_evento(session: Session, destino: str, evento: str) -> None:
    caso = caso_en(session, escenario(session), "Caso creado")
    _cambiar_estado(session, caso.id_caso, destino)
    assert [e[0] for e in eventos_de(session, caso.id_caso)] == ["CASO_CREADO", evento]


def test_registrado_no_duplica_el_evento_del_diagnostico(session: Session) -> None:
    """Pasar a Registrado lo anota trg_diag_evento: este trigger no agrega un segundo evento."""
    caso = caso_en(session, escenario(session), "En revisión")
    _cambiar_estado(session, caso.id_caso, "Registrado")
    assert [e[0] for e in eventos_de(session, caso.id_caso)] == ["CASO_CREADO"]


def test_el_flujo_completo_deja_la_linea_de_tiempo_en_orden(session: Session) -> None:
    caso = caso_en(session, escenario(session), "Caso creado")
    for estado in ("En análisis", "Pendiente de revisión", "En revisión", "Registrado", "Cerrado"):
        _cambiar_estado(session, caso.id_caso, estado)
    assert [e[0] for e in eventos_de(session, caso.id_caso)] == [
        "CASO_CREADO",
        "ANALISIS_CHATBOT",
        "ENVIADO_A_REVISION",
        "TECNICO_ASIGNADO",
        "CASO_CERRADO",
    ]


def test_sin_cambio_de_estado_no_hay_evento(session: Session) -> None:
    caso = caso_en(session, escenario(session), "Caso creado")
    _cambiar_estado(session, caso.id_caso, "Caso creado")  # mismo estado
    session.execute(
        text("UPDATE casos SET descripcion_zona_afectada = 'Hojas bajas' WHERE id_caso = :c"), {"c": caso.id_caso}
    )
    session.execute(
        text("UPDATE casos SET advertencia_inactividad_enviada = 'S' WHERE id_caso = :c"), {"c": caso.id_caso}
    )
    assert [e[0] for e in eventos_de(session, caso.id_caso)] == ["CASO_CREADO"]


def test_el_evento_de_estado_sin_contexto_es_del_sistema(session: Session) -> None:
    caso = caso_en(session, escenario(session), "Caso creado")
    _cambiar_estado(session, caso.id_caso, "En análisis")
    assert eventos_de(session, caso.id_caso)[-1] == ("ANALISIS_CHATBOT", "SISTEMA", None)


def test_el_evento_de_estado_anota_al_actor_del_contexto(session: Session) -> None:
    caso = caso_en(session, escenario(session), "Pendiente de revisión")
    lider = crear_lider(session)
    llamar(session, sp.ESTABLECER_ACTOR, tipo="LIDER", id=lider.id_lider_tecnico)
    _cambiar_estado(session, caso.id_caso, "En revisión")
    assert eventos_de(session, caso.id_caso)[-1] == ("TECNICO_ASIGNADO", "LIDER", lider.id_lider_tecnico)


def test_el_evento_de_estado_usa_el_reloj_simulado(session: Session) -> None:
    caso = caso_en(session, escenario(session), "Caso creado")
    llamar(session, sp.ESTABLECER_AHORA, ahora="2026-03-01 08:00:00+00")
    _cambiar_estado(session, caso.id_caso, "En análisis")
    fecha = session.scalar(
        text("SELECT fecha_evento FROM eventos WHERE id_caso = :c AND tipo_evento = 'ANALISIS_CHATBOT'"),
        {"c": caso.id_caso},
    )
    assert fecha == datetime(2026, 3, 1, 8, 0, tzinfo=UTC)


# --- trg_diag_evento ---------------------------------------------------------------------------------------------


def _completar(session: Session, id_caso: str, resultado: str = "NO_IDENTIFICADO") -> None:
    session.execute(
        text("UPDATE diagnosticos SET resultado_tecnico = :r, fecha_diagnostico = now() WHERE id_caso = :c"),
        {"r": resultado, "c": id_caso},
    )


def test_crear_el_diagnostico_vacio_no_anota_evento(session: Session) -> None:
    caso = caso_en_revision(session, escenario(session), crear_tecnico(session))
    assert "DIAGNOSTICO_REGISTRADO" not in [e[0] for e in eventos_de(session, caso.id_caso)]


def test_completar_el_diagnostico_anota_un_evento_con_el_tecnico(session: Session) -> None:
    tecnico = crear_tecnico(session)
    caso = caso_en_revision(session, escenario(session), tecnico)
    _completar(session, caso.id_caso)
    assert eventos_de(session, caso.id_caso)[-1] == ("DIAGNOSTICO_REGISTRADO", "TECNICO", tecnico.id_tecnico)


def test_editar_un_diagnostico_ya_registrado_no_repite_el_evento(session: Session) -> None:
    caso = caso_en_revision(session, escenario(session), crear_tecnico(session))
    _completar(session, caso.id_caso)
    session.execute(
        text("UPDATE diagnosticos SET observaciones_diagnostico = 'Revisar en 15 días' WHERE id_caso = :c"),
        {"c": caso.id_caso},
    )
    session.execute(
        text("UPDATE diagnosticos SET resultado_tecnico = 'REQUIERE_VISITA' WHERE id_caso = :c"), {"c": caso.id_caso}
    )
    assert [e[0] for e in eventos_de(session, caso.id_caso)].count("DIAGNOSTICO_REGISTRADO") == 1


# --- trg_diag_tecnico_activo -------------------------------------------------------------------------------------


def test_el_tecnico_activo_asignado_puede_completar(session: Session) -> None:
    caso = caso_en_revision(session, escenario(session), crear_tecnico(session))
    _completar(session, caso.id_caso)
    assert session.scalar(text("SELECT resultado_tecnico FROM diagnosticos WHERE id_caso = :c"), {"c": caso.id_caso})


def test_sin_asignacion_no_se_puede_completar(session: Session) -> None:
    caso = caso_en(session, escenario(session), "En revisión")
    session.add(Diagnostico(id_caso=caso.id_caso))
    session.flush()
    falla_con(session, sp.SqlState.NO_ES_EL_TECNICO_ASIGNADO, lambda: _completar(session, caso.id_caso))


def test_con_la_asignacion_cerrada_no_se_puede_completar(session: Session) -> None:
    caso = caso_en(session, escenario(session), "En revisión")
    asignar(session, caso, crear_tecnico(session), vigente=False)
    session.add(Diagnostico(id_caso=caso.id_caso))
    session.flush()
    falla_con(session, sp.SqlState.NO_ES_EL_TECNICO_ASIGNADO, lambda: _completar(session, caso.id_caso))


def test_un_tecnico_deshabilitado_no_puede_completar(session: Session) -> None:
    tecnico = crear_tecnico(session)
    caso = caso_en_revision(session, escenario(session), tecnico)
    tecnico.estado_cuenta_tecnico = "DESHABILITADO"
    session.flush()
    falla_con(session, sp.SqlState.NO_ES_EL_TECNICO_ASIGNADO, lambda: _completar(session, caso.id_caso))


def test_insertar_un_diagnostico_ya_lleno_tambien_se_valida(session: Session) -> None:
    caso = caso_en(session, escenario(session), "En revisión")
    falla_con(
        session,
        sp.SqlState.NO_ES_EL_TECNICO_ASIGNADO,
        lambda: session.execute(
            text(
                "INSERT INTO diagnosticos (id_diagnostico, id_caso, resultado_tecnico, fecha_diagnostico) "
                "VALUES ('DIA-90000001', :c, 'NO_IDENTIFICADO', now())"
            ),
            {"c": caso.id_caso},
        ),
    )


def test_la_fila_vacia_no_necesita_tecnico(session: Session) -> None:
    """Solo se exige técnico al completar el resultado: el diagnóstico vacío nace con el caso."""
    caso = caso_en(session, escenario(session), "Caso creado")
    session.add(Diagnostico(id_caso=caso.id_caso))
    session.flush()
    session.execute(
        text("UPDATE diagnosticos SET observaciones_diagnostico = 'nota' WHERE id_caso = :c"), {"c": caso.id_caso}
    )


# --- trg_param_transmisible --------------------------------------------------------------------------------------


def test_una_categoria_transmisible_puede_tener_parametros(session: Session) -> None:
    crear_parametro(session, crear_categoria(session, "Transmisible de prueba", "S"))


def test_una_categoria_no_transmisible_no_puede_tener_parametros(session: Session) -> None:
    categoria = crear_categoria(session, "No transmisible", "N")
    falla_con(session, sp.SqlState.DATOS_INVALIDOS, lambda: crear_parametro(session, categoria))


def test_las_categorias_sembradas_respetan_la_regla(session: Session) -> None:
    """Magnesio (no transmisible) no admite parámetros; Trips ya tiene el suyo."""
    magnesio = session.scalar(
        text("SELECT id_categoria FROM categorias_fitosanitarias WHERE nombre_categoria = 'Deficiencia de magnesio'")
    )
    falla_con(
        session,
        sp.SqlState.DATOS_INVALIDOS,
        lambda: session.execute(
            text(
                "INSERT INTO parametros_epidemiologicos (id_parametro, id_categoria, numero_minimo_casos, "
                "distancia_km, intervalo_dias, fecha_actualizacion) VALUES ('PAR-900001', :c, 3, 1, 30, now())"
            ),
            {"c": magnesio},
        ),
    )


def test_cambiar_el_parametro_a_una_categoria_no_transmisible_se_rechaza(session: Session) -> None:
    parametro = crear_parametro(session, crear_categoria(session, "Transmisible", "S"))
    otra = crear_categoria(session, "No transmisible", "N")
    falla_con(
        session,
        sp.SqlState.DATOS_INVALIDOS,
        lambda: session.execute(
            text("UPDATE parametros_epidemiologicos SET id_categoria = :c WHERE id_parametro = :p"),
            {"c": otra.id_categoria, "p": parametro.id_parametro},
        ),
    )


def test_editar_otros_campos_del_parametro_no_dispara_la_regla(session: Session) -> None:
    parametro = crear_parametro(session, crear_categoria(session, "Transmisible", "S"))
    session.execute(
        text("UPDATE parametros_epidemiologicos SET distancia_km = 5, numero_minimo_casos = 2 WHERE id_parametro = :p"),
        {"p": parametro.id_parametro},
    )


# --- trg_auditoria_inmutable -------------------------------------------------------------------------------------


def _auditar(session: Session) -> str:
    llamar(
        session,
        sp.REGISTRAR_AUDITORIA,
        origen="SISTEMA",
        id_actor=None,
        accion="PRUEBA",
        entidad="casos",
        descripcion="Registro de prueba",
        valor_anterior=None,
        valor_nuevo=None,
        resultado="EXITO",
        fecha=None,
    )
    return str(session.scalar(text("SELECT id_auditoria FROM auditoria ORDER BY id_auditoria DESC LIMIT 1")))


def test_se_puede_insertar_en_la_auditoria(session: Session) -> None:
    assert _auditar(session).startswith("AUD-")


def test_no_se_puede_modificar_la_auditoria(session: Session) -> None:
    id_auditoria = _auditar(session)
    falla_con(
        session,
        sp.SqlState.AUDITORIA_INMUTABLE,
        lambda: session.execute(
            text("UPDATE auditoria SET descripcion = 'alterada' WHERE id_auditoria = :a"), {"a": id_auditoria}
        ),
    )
    assert session.scalar(text("SELECT descripcion FROM auditoria WHERE id_auditoria = :a"), {"a": id_auditoria}) == (
        "Registro de prueba"
    )


def test_no_se_puede_borrar_la_auditoria(session: Session) -> None:
    id_auditoria = _auditar(session)
    falla_con(
        session,
        sp.SqlState.AUDITORIA_INMUTABLE,
        lambda: session.execute(text("DELETE FROM auditoria WHERE id_auditoria = :a"), {"a": id_auditoria}),
    )
    assert session.scalar(text("SELECT count(*) FROM auditoria WHERE id_auditoria = :a"), {"a": id_auditoria}) == 1


def test_ni_siquiera_un_update_que_no_cambia_nada(session: Session) -> None:
    id_auditoria = _auditar(session)
    falla_con(
        session,
        sp.SqlState.AUDITORIA_INMUTABLE,
        lambda: session.execute(
            text("UPDATE auditoria SET resultado = resultado WHERE id_auditoria = :a"), {"a": id_auditoria}
        ),
    )


def test_el_orm_tampoco_puede_modificarla(session: Session) -> None:
    id_auditoria = _auditar(session)
    registro = session.get(Auditoria, id_auditoria)
    assert registro is not None
    registro.descripcion = "alterada por el ORM"
    falla_con(session, sp.SqlState.AUDITORIA_INMUTABLE, session.flush)
    session.expire_all()
