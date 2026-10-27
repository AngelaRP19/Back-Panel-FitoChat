from collections.abc import Callable
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.infrastructure.db.models import (
    Caso,
    CategoriaFitosanitaria,
    CoincidenciaCategoria,
    Especie,
    ParametroEpidemiologico,
)
from tests.infrastructure.db.factories import ahora, escenario


def _viola(session: Session, constraint: str, accion: Callable[[], None]) -> None:
    """Ejecuta la acción en un savepoint y comprueba que falla por la restricción indicada."""
    with pytest.raises(IntegrityError) as exc:
        with session.begin_nested():
            accion()
            session.flush()
    assert exc.value.orig.diag.constraint_name == constraint  # type: ignore[union-attr]


# --- únicos ----------------------------------------------------------------------------------


def test_nombre_de_especie_unico(session: Session) -> None:
    session.add(Especie(nombre_especie="Duplicada"))
    session.flush()
    _viola(session, "uq_esp_nombre", lambda: session.add(Especie(nombre_especie="Duplicada")))


def test_nombre_de_categoria_unico(session: Session) -> None:
    session.add(CategoriaFitosanitaria(nombre_categoria="Repetida", es_transmisible="N"))
    session.flush()
    _viola(
        session,
        "uq_cat_nombre",
        lambda: session.add(CategoriaFitosanitaria(nombre_categoria="Repetida", es_transmisible="S")),
    )


def test_telefono_de_productor_unico(session: Session) -> None:
    datos = escenario(session)
    otro = type(datos.productor)(
        nombre_productor="Otro",
        telefono_productor=datos.productor.telefono_productor,
        estado_cuenta_productor="ACTIVO",
        fecha_registro_productor=datos.productor.fecha_registro_productor,
    )
    _viola(session, "uq_prod_telefono", lambda: session.add(otro))


def test_un_parametro_por_categoria(session: Session) -> None:
    cat = CategoriaFitosanitaria(nombre_categoria="Con parámetro", es_transmisible="S")
    session.add(cat)
    session.flush()

    def parametro() -> ParametroEpidemiologico:
        # Por id_categoria y no por la relación: la relación 1:1 desasociaría el primer parámetro.
        return ParametroEpidemiologico(
            id_categoria=cat.id_categoria,
            numero_minimo_casos=3,
            distancia_km=Decimal("3.5"),
            intervalo_dias=30,
            fecha_actualizacion=ahora(),
        )

    session.add(parametro())
    session.flush()
    _viola(session, "uq_par_categoria", lambda: session.add(parametro()))


# --- CHECK -----------------------------------------------------------------------------------


def test_es_transmisible_solo_s_o_n(session: Session) -> None:
    _viola(
        session,
        "ck_categorias_fitosanitarias_es_transmisible_sn",
        lambda: session.add(CategoriaFitosanitaria(nombre_categoria="Mala bandera", es_transmisible="X")),
    )


def test_advertencia_inactividad_solo_s_o_n(session: Session) -> None:
    datos = escenario(session)
    caso = _caso(
        datos.especie.id_especie,
        datos.cultivo.id_cultivo,
        datos.estado.id_estado,
        datos.identificacion.id_identificacion,
    )
    session.add(caso)
    session.flush()
    _viola(
        session,
        "ck_casos_advertencia_inactividad_enviada_sn",
        lambda: session.execute(
            text("UPDATE casos SET advertencia_inactividad_enviada = 'X' WHERE id_caso = :id"), {"id": caso.id_caso}
        ),
    )


@pytest.mark.parametrize("porcentaje", [Decimal("-0.01"), Decimal("100.01")])
def test_porcentaje_de_coincidencia_entre_0_y_100(session: Session, porcentaje: Decimal) -> None:
    datos = escenario(session)
    cat = CategoriaFitosanitaria(nombre_categoria="Para porcentaje", es_transmisible="N")
    session.add(cat)
    session.flush()
    _viola(
        session,
        "ck_coincidencias_categoria_porcentaje_rango",
        lambda: session.add(
            CoincidenciaCategoria(
                identificacion=datos.identificacion, categoria=cat, porcentaje_coincidencia=porcentaje
            )
        ),
    )


@pytest.mark.parametrize("porcentaje", [Decimal("0"), Decimal("100")])
def test_porcentaje_en_los_limites_es_valido(session: Session, porcentaje: Decimal) -> None:
    datos = escenario(session)
    cat = CategoriaFitosanitaria(nombre_categoria=f"Límite {porcentaje}", es_transmisible="N")
    session.add(
        CoincidenciaCategoria(identificacion=datos.identificacion, categoria=cat, porcentaje_coincidencia=porcentaje)
    )
    session.flush()


@pytest.mark.parametrize(
    ("campo", "constraint"),
    [
        ("numero_minimo_casos", "ck_parametros_epidemiologicos_numero_minimo_casos_rango"),
        ("intervalo_dias", "ck_parametros_epidemiologicos_intervalo_dias_rango"),
    ],
)
def test_parametros_enteros_al_menos_1(session: Session, campo: str, constraint: str) -> None:
    cat = CategoriaFitosanitaria(nombre_categoria=f"Param {campo}", es_transmisible="S")
    valores = {"numero_minimo_casos": 3, "intervalo_dias": 30, campo: 0}
    _viola(
        session,
        constraint,
        lambda: session.add(
            ParametroEpidemiologico(categoria=cat, distancia_km=Decimal("3.5"), fecha_actualizacion=ahora(), **valores)
        ),
    )


# --- FK compuesta de casos -------------------------------------------------------------------


def _caso(datos_especie_id: str, cultivo_id: str, estado_id: str, identificacion_id: str) -> Caso:
    return Caso(
        id_cultivo=cultivo_id,
        id_especie=datos_especie_id,
        id_estado=estado_id,
        id_identificacion_preliminar=identificacion_id,
        fecha_creacion_caso=ahora(),
    )


def test_caso_con_par_cultivo_especie_valido(session: Session) -> None:
    datos = escenario(session)
    caso = _caso(
        datos.especie.id_especie,
        datos.cultivo.id_cultivo,
        datos.estado.id_estado,
        datos.identificacion.id_identificacion,
    )
    session.add(caso)
    session.flush()
    assert caso.advertencia_inactividad_enviada == "N"  # server_default
    assert caso.cultivo_especie.especie is datos.especie


def test_caso_con_especie_no_asociada_al_cultivo_falla(session: Session) -> None:
    """La especie existe y el cultivo existe, pero el PAR no está en cultivo_especie."""
    datos = escenario(session)
    caso = _caso(
        datos.otra_especie.id_especie,
        datos.cultivo.id_cultivo,
        datos.estado.id_estado,
        datos.identificacion.id_identificacion,
    )
    _viola(session, "fk_cas_cul_esp", lambda: session.add(caso))
