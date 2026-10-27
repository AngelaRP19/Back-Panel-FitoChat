"""Las seis vistas del Avance 5 (guía §6.6)."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.infrastructure.db import procedimientos as sp
from app.infrastructure.db.models import CategoriaFitosanitaria, CoincidenciaCategoria
from tests.infrastructure.db.factories import (
    ahora,
    asignar,
    caso_con_diagnostico,
    caso_en,
    caso_en_revision,
    casos_en_todos_los_estados,
    crear_lider,
    crear_tecnico,
    escenario,
    llamar,
)

pytestmark = pytest.mark.usefixtures("semillas")


def categoria(session: Session, nombre: str) -> CategoriaFitosanitaria:
    return session.scalars(
        select(CategoriaFitosanitaria).where(CategoriaFitosanitaria.nombre_categoria == nombre)
    ).one()


def fila(session: Session, vista: str, id_caso: str) -> dict[str, object]:
    resultado = session.execute(text(f"SELECT * FROM {vista} WHERE id_caso = :c"), {"c": id_caso}).mappings().all()
    assert len(resultado) == 1, f"{vista} debe devolver una fila por caso, devolvió {len(resultado)}"
    return dict(resultado[0])


def coincidir(session: Session, identificacion: str, **porcentajes: str) -> None:
    for nombre, porcentaje in porcentajes.items():
        session.add(
            CoincidenciaCategoria(
                id_identificacion=identificacion,
                id_categoria=categoria(session, nombre.replace("_", " ")).id_categoria,
                porcentaje_coincidencia=Decimal(porcentaje),
            )
        )
    session.flush()


# --- vw_casos_detalle --------------------------------------------------------------------------------------------


def test_detalle_junta_productor_finca_cultivo_especie_y_estado(session: Session) -> None:
    datos = escenario(session)
    caso = caso_en(session, datos, "Pendiente de revisión")
    v = fila(session, "vw_casos_detalle", caso.id_caso)
    assert v["nombre_estado"] == "Pendiente de revisión"
    assert (v["nombre_productor"], v["telefono_productor"]) == ("Productor de prueba", "573001112233")
    assert (v["nombre_finca"], v["vereda_sector_finca"]) == ("La Esperanza", "Centro")
    assert (v["nombre_cultivo"], v["tipo_cultivo"]) == ("Lote 1", "CAMPO_ABIERTO")
    assert v["nombre_especie"] == "Especie test A"
    assert v["id_productor"] == datos.productor.id_productor and v["id_finca"] == datos.finca.id_finca


def test_detalle_trae_la_ubicacion_de_la_finca(session: Session) -> None:
    caso = caso_en(session, escenario(session), "Caso creado")
    x, y = session.execute(
        text("SELECT ST_X(ubicacion_finca), ST_Y(ubicacion_finca) FROM vw_casos_detalle WHERE id_caso = :c"),
        {"c": caso.id_caso},
    ).one()
    assert (round(x, 4), round(y, 4)) == (-73.171, 5.2832)


def test_detalle_muestra_la_categoria_con_mas_coincidencia(session: Session) -> None:
    datos = escenario(session)
    coincidir(session, datos.identificacion.id_identificacion, Botrytis="62.50", Trips="25.00", Fusarium="12.50")
    caso = caso_en(session, datos, "Pendiente de revisión")
    v = fila(session, "vw_casos_detalle", caso.id_caso)
    assert v["nombre_categoria_principal"] == "Botrytis"
    assert v["porcentaje_principal"] == Decimal("62.50")
    assert v["resultado_analisis"] == "IDENTIFICADO"


def test_en_un_empate_gana_la_categoria_de_menor_id(session: Session) -> None:
    datos = escenario(session)
    coincidir(session, datos.identificacion.id_identificacion, Trips="50.00", Botrytis="50.00")
    caso = caso_en(session, datos, "Pendiente de revisión")
    assert fila(session, "vw_casos_detalle", caso.id_caso)["nombre_categoria_principal"] == "Botrytis"  # CF001


def test_detalle_de_un_caso_sin_analisis_ni_tecnico(session: Session) -> None:
    caso = caso_en(session, escenario(session), "Caso creado", con_identificacion=False)
    v = fila(session, "vw_casos_detalle", caso.id_caso)
    assert v["id_identificacion_preliminar"] is None and v["id_categoria_principal"] is None
    assert v["porcentaje_principal"] is None
    assert v["id_tecnico"] is None and v["resultado_tecnico"] is None


def test_detalle_muestra_solo_el_tecnico_de_la_asignacion_vigente(session: Session) -> None:
    datos = escenario(session)
    caso = caso_en(session, datos, "En revisión")
    primero, segundo = crear_tecnico(session, 1), crear_tecnico(session, 2)
    asignar(session, caso, primero, vigente=False)  # reasignado: ya no es el responsable
    asignar(session, caso, segundo)
    v = fila(session, "vw_casos_detalle", caso.id_caso)  # una sola fila aunque haya dos asignaciones
    assert (v["id_tecnico"], v["nombre_tecnico"]) == (segundo.id_tecnico, "Técnico 2")


def test_detalle_una_fila_por_caso_con_varias_coincidencias(session: Session) -> None:
    datos = escenario(session)
    coincidir(session, datos.identificacion.id_identificacion, Botrytis="40.00", Trips="30.00", Roña="20.00")
    casos = [caso_en(session, datos, "Pendiente de revisión") for _ in range(3)]
    filas = session.execute(
        text("SELECT id_caso FROM vw_casos_detalle WHERE id_caso = ANY(:ids)"), {"ids": [c.id_caso for c in casos]}
    ).all()
    assert len(filas) == 3


def test_detalle_incluye_el_diagnostico_registrado(session: Session) -> None:
    tecnico = crear_tecnico(session)
    caso = caso_en_revision(session, escenario(session), tecnico)
    trips = categoria(session, "Trips")
    session.execute(
        text(
            "UPDATE diagnosticos SET resultado_tecnico = 'CONFIRMADO', id_categoria_confirmada = :c, "
            "fecha_diagnostico = now() WHERE id_caso = :i"
        ),
        {"c": trips.id_categoria, "i": caso.id_caso},
    )
    v = fila(session, "vw_casos_detalle", caso.id_caso)
    assert (v["resultado_tecnico"], v["nombre_categoria_confirmada"]) == ("CONFIRMADO", "Trips")


def test_detalle_con_diagnostico_vacio_no_muestra_resultado(session: Session) -> None:
    caso = caso_en_revision(session, escenario(session), crear_tecnico(session))
    v = fila(session, "vw_casos_detalle", caso.id_caso)
    assert v["resultado_tecnico"] is None and v["fecha_diagnostico"] is None


def test_detalle_trae_las_fechas_del_ciclo_de_vida(session: Session) -> None:
    caso = caso_en(session, escenario(session), "Cerrado")
    caso.fecha_envio_panel = ahora() - timedelta(days=3)
    caso.fecha_cierre = ahora()
    session.flush()
    v = fila(session, "vw_casos_detalle", caso.id_caso)
    assert v["fecha_envio_panel"] == caso.fecha_envio_panel and v["fecha_cierre"] == caso.fecha_cierre
    assert v["ultima_actividad"] is not None


# --- vw_casos_pendientes_revision --------------------------------------------------------------------------------


def test_pendientes_solo_trae_los_pendientes_de_revision(session: Session) -> None:
    datos = escenario(session)
    por_estado = casos_en_todos_los_estados(session, datos)
    ids = session.scalars(
        text("SELECT id_caso FROM vw_casos_pendientes_revision WHERE id_caso = ANY(:ids)"),
        {"ids": [c.id_caso for c in por_estado.values()]},
    ).all()
    assert ids == [por_estado["Pendiente de revisión"].id_caso]


def test_pendientes_calcula_las_horas_de_espera(session: Session) -> None:
    caso = caso_en(session, escenario(session), "Pendiente de revisión")
    caso.fecha_envio_panel = ahora() - timedelta(hours=5)
    session.flush()
    horas = float(fila(session, "vw_casos_pendientes_revision", caso.id_caso)["horas_en_espera"])  # type: ignore[arg-type]
    assert 4.99 < horas < 5.1


def test_un_caso_asignado_sale_de_pendientes(session: Session) -> None:
    caso = caso_en(session, escenario(session), "Pendiente de revisión")
    assert (
        session.scalar(
            text("SELECT count(*) FROM vw_casos_pendientes_revision WHERE id_caso = :c"), {"c": caso.id_caso}
        )
        == 1
    )
    session.execute(
        text("UPDATE casos SET id_estado = fn_id_estado('En revisión') WHERE id_caso = :c"), {"c": caso.id_caso}
    )
    assert (
        session.scalar(
            text("SELECT count(*) FROM vw_casos_pendientes_revision WHERE id_caso = :c"), {"c": caso.id_caso}
        )
        == 0
    )


# --- vw_diagnosticos_detalle -------------------------------------------------------------------------------------


def test_diagnosticos_no_incluye_las_filas_vacias(session: Session) -> None:
    caso = caso_en_revision(session, escenario(session), crear_tecnico(session))
    assert (
        session.scalar(text("SELECT count(*) FROM vw_diagnosticos_detalle WHERE id_caso = :c"), {"c": caso.id_caso})
        == 0
    )


def test_diagnosticos_trae_autor_y_categoria(session: Session) -> None:
    datos = escenario(session)
    trips = categoria(session, "Trips")
    caso = caso_con_diagnostico(session, datos, trips)
    d = fila(session, "vw_diagnosticos_detalle", caso.id_caso)
    assert (d["resultado_tecnico"], d["nombre_categoria_confirmada"], d["es_transmisible"]) == (
        "CONFIRMADO",
        "Trips",
        "S",
    )
    assert str(d["nombre_tecnico_autor"]).startswith("Técnico ") and str(d["id_tecnico_autor"]).startswith("TEC-")


def test_diagnosticos_sin_categoria_para_no_identificado(session: Session) -> None:
    caso = caso_con_diagnostico(session, escenario(session), None, resultado="NO_IDENTIFICADO")
    d = fila(session, "vw_diagnosticos_detalle", caso.id_caso)
    assert d["resultado_tecnico"] == "NO_IDENTIFICADO" and d["id_categoria_confirmada"] is None


# --- vw_casos_confirmados_transmisibles --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("categoria_nombre", "resultado", "cuenta"),
    [
        ("Trips", "CONFIRMADO", True),
        ("Trips", "OTRA_PATOLOGIA", True),  # el técnico determinó una categoría transmisible distinta a la preliminar
        ("Deficiencia de magnesio", "CONFIRMADO", False),  # no transmisible
        ("Botrytis", "CONFIRMADO", False),
        ("Fusarium", "OTRA_PATOLOGIA", False),
        (None, "NO_IDENTIFICADO", False),
        (None, "REQUIERE_VISITA", False),
        (None, None, False),  # diagnóstico vacío
    ],
)
def test_solo_cuentan_los_confirmados_de_categorias_transmisibles(
    session: Session, categoria_nombre: str | None, resultado: str | None, cuenta: bool
) -> None:
    cat = categoria(session, categoria_nombre) if categoria_nombre else None
    caso = caso_con_diagnostico(session, escenario(session), cat, resultado=resultado)
    hay = session.scalar(
        text("SELECT count(*) FROM vw_casos_confirmados_transmisibles WHERE id_caso = :c"), {"c": caso.id_caso}
    )
    assert hay == (1 if cuenta else 0)


def test_confirmados_trae_la_ubicacion_de_la_finca(session: Session) -> None:
    caso = caso_con_diagnostico(session, escenario(session), categoria(session, "Trips"), lat="5.3000", lon="-73.1500")
    x, y = session.execute(
        text(
            "SELECT ST_X(ubicacion_finca), ST_Y(ubicacion_finca) FROM vw_casos_confirmados_transmisibles "
            "WHERE id_caso = :c"
        ),
        {"c": caso.id_caso},
    ).one()
    assert (round(x, 4), round(y, 4)) == (-73.15, 5.3)


# --- vw_ultima_actividad_caso ------------------------------------------------------------------------------------


def test_actividad_solo_trae_los_casos_que_maneja_el_chatbot(session: Session) -> None:
    datos = escenario(session)
    por_estado = casos_en_todos_los_estados(session, datos)
    ids = set(
        session.scalars(
            text("SELECT id_caso FROM vw_ultima_actividad_caso WHERE id_caso = ANY(:ids)"),
            {"ids": [c.id_caso for c in por_estado.values()]},
        )
    )
    assert ids == {por_estado["Caso creado"].id_caso, por_estado["En análisis"].id_caso}


def test_actividad_calcula_las_horas_de_inactividad(session: Session) -> None:
    caso = caso_en(session, escenario(session), "En análisis")
    caso.ultima_actividad = ahora() - timedelta(hours=50)
    session.flush()
    v = fila(session, "vw_ultima_actividad_caso", caso.id_caso)
    assert 49.9 < float(v["horas_inactivo"]) < 50.1  # type: ignore[arg-type]
    assert v["advertencia_inactividad_enviada"] == "N" and v["telefono_productor"] == "573001112233"


def test_actividad_refleja_la_advertencia_enviada(session: Session) -> None:
    caso = caso_en(session, escenario(session), "Caso creado")
    llamar(session, sp.MARCAR_ADVERTENCIA_ENVIADA, id_caso=caso.id_caso)
    assert fila(session, "vw_ultima_actividad_caso", caso.id_caso)["advertencia_inactividad_enviada"] == "S"


# --- vw_auditoria_detalle ----------------------------------------------------------------------------------------


def _auditar(session: Session, origen: str, id_actor: str | None) -> None:
    llamar(
        session,
        sp.REGISTRAR_AUDITORIA,
        origen=origen,
        id_actor=id_actor,
        accion="PRUEBA",
        entidad="casos",
        descripcion=f"Acción de {origen}",
        valor_anterior=None,
        valor_nuevo=None,
        resultado="EXITO",
        fecha=None,
    )


def test_auditoria_detalle_muestra_el_nombre_del_autor_segun_el_origen(session: Session) -> None:
    lider, tecnico = crear_lider(session), crear_tecnico(session)
    _auditar(session, "LIDER", lider.id_lider_tecnico)
    _auditar(session, "TECNICO", tecnico.id_tecnico)
    _auditar(session, "SISTEMA", None)
    filas = session.execute(
        text("SELECT origen, id_autor, nombre_autor FROM vw_auditoria_detalle ORDER BY id_auditoria DESC LIMIT 3")
    ).all()
    assert {f[0]: (f[1], f[2]) for f in filas} == {
        "LIDER": (lider.id_lider_tecnico, "Líder 1"),
        "TECNICO": (tecnico.id_tecnico, "Técnico 1"),
        "SISTEMA": (None, "Sistema"),
    }
