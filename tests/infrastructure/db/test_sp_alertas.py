"""sp_evaluar_alerta: casos confirmados de una categoría transmisible, cercanos y recientes → alerta GENERADA."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.infrastructure.db import procedimientos as sp
from app.infrastructure.db.models import CategoriaFitosanitaria
from tests.infrastructure.db.factories import (
    Escenario,
    caso_con_diagnostico,
    crear_categoria,
    crear_parametro,
    distancia_km,
    escenario,
    llamar,
)

pytestmark = pytest.mark.usefixtures("semillas")

AHORA = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
# Cerca de Zetaquirá. 0,001 grado de latitud ≈ 110 m, así que estos puntos distan unos 0,2 km entre sí.
CERCA = [("5.2800", "-73.1700"), ("5.2810", "-73.1700"), ("5.2820", "-73.1700")]
# Unos 3,4 km al norte del primero (dentro del radio de 3,5 km de los tres casos cercanos).
A_3_4_KM = ("5.3107", "-73.1700")
# Más de 3,5 km de CERCA[0] y de CERCA[1] (3,76 y 3,65 km): fuera del radio de cualquiera de ellos.
A_3_7_KM = ("5.3140", "-73.1700")
# Lejísimos de todos.
LEJOS = ("5.3500", "-73.1700")


def trips(session: Session) -> CategoriaFitosanitaria:
    consulta = select(CategoriaFitosanitaria).where(CategoriaFitosanitaria.nombre_categoria == "Trips")
    return session.scalars(consulta).one()


def evaluar(session: Session, categoria: CategoriaFitosanitaria | None = None, fecha: datetime = AHORA) -> None:
    llamar(session, sp.EVALUAR_ALERTA, id_categoria=categoria.id_categoria if categoria else None, fecha=fecha)


def alertas(session: Session) -> list[dict[str, object]]:
    filas = session.execute(text("SELECT * FROM alertas ORDER BY id_alerta")).mappings().all()
    return [dict(f) for f in filas]


def casos_de(session: Session, id_alerta: object) -> set[str]:
    return set(session.scalars(text("SELECT id_caso FROM alerta_casos WHERE id_alerta = :a"), {"a": id_alerta}))


def confirmados(session: Session, datos: Escenario, categoria: CategoriaFitosanitaria, puntos: list[tuple[str, str]],
                dias_atras: float = 1, **extra: object) -> list[str]:  # fmt: skip
    fecha = AHORA - timedelta(days=dias_atras)
    return [
        caso_con_diagnostico(session, datos, categoria, lat=lat, lon=lon, fecha=fecha, **extra).id_caso  # type: ignore[arg-type]
        for lat, lon in puntos
    ]


# --- cuándo se crea una alerta -----------------------------------------------------------------------------------


def test_tres_casos_cercanos_y_recientes_generan_una_alerta(session: Session) -> None:
    datos = escenario(session)
    ids = confirmados(session, datos, trips(session), CERCA)
    evaluar(session)
    [alerta] = alertas(session)
    assert alerta["estado_alerta"] == "GENERADA"
    assert str(alerta["id_alerta"]).startswith("ALR-") and len(str(alerta["id_alerta"])) == 10
    assert casos_de(session, alerta["id_alerta"]) == set(ids)


def test_la_alerta_guarda_los_parametros_con_que_se_creo(session: Session) -> None:
    datos = escenario(session)
    confirmados(session, datos, trips(session), CERCA)
    evaluar(session)
    [alerta] = alertas(session)
    assert (alerta["numero_minimo_casos"], alerta["distancia_km"], alerta["intervalo_dias"]) == (3, Decimal("3.50"), 30)
    assert alerta["fecha_generacion_alerta"] == AHORA
    assert alerta["id_categoria"] == trips(session).id_categoria
    assert alerta["id_lider_gestion"] is None and alerta["recomendacion"] is None


def test_la_alerta_conserva_sus_parametros_aunque_el_lider_los_cambie(session: Session) -> None:
    datos = escenario(session)
    confirmados(session, datos, trips(session), CERCA)
    evaluar(session)
    session.execute(text("UPDATE parametros_epidemiologicos SET distancia_km = 10, numero_minimo_casos = 2"))
    assert alertas(session)[0]["distancia_km"] == Decimal("3.50")
    assert alertas(session)[0]["numero_minimo_casos"] == 3


def test_dos_casos_no_alcanzan_el_minimo(session: Session) -> None:
    datos = escenario(session)
    confirmados(session, datos, trips(session), CERCA[:2])
    evaluar(session)
    assert alertas(session) == []


def test_sin_casos_no_hay_alerta(session: Session) -> None:
    evaluar(session)
    assert alertas(session) == []


# --- distancia ---------------------------------------------------------------------------------------------------


def test_los_puntos_de_prueba_estan_donde_se_dice(session: Session) -> None:
    datos = escenario(session)
    c0, c1, dentro, fuera, lejos = (
        caso_con_diagnostico(session, datos, trips(session), lat=lat, lon=lon)
        for lat, lon in (CERCA[0], CERCA[1], A_3_4_KM, A_3_7_KM, LEJOS)
    )
    assert 3.3 < distancia_km(session, c0, dentro) < 3.5
    assert distancia_km(session, c0, fuera) > 3.5 and distancia_km(session, c1, fuera) > 3.5
    assert distancia_km(session, c0, lejos) > 6


def test_un_caso_a_3_4_km_cuenta(session: Session) -> None:
    datos = escenario(session)
    ids = confirmados(session, datos, trips(session), [CERCA[0], CERCA[1], A_3_4_KM])
    evaluar(session)
    [alerta] = alertas(session)
    assert casos_de(session, alerta["id_alerta"]) == set(ids)


def test_un_caso_a_mas_de_3_5_km_no_cuenta(session: Session) -> None:
    datos = escenario(session)
    confirmados(session, datos, trips(session), [CERCA[0], CERCA[1], A_3_7_KM])
    evaluar(session)
    assert alertas(session) == []


def test_un_caso_lejano_no_se_suma_al_grupo(session: Session) -> None:
    datos = escenario(session)
    ids = confirmados(session, datos, trips(session), [*CERCA, LEJOS])
    evaluar(session)
    [alerta] = alertas(session)
    assert casos_de(session, alerta["id_alerta"]) == set(ids[:3])  # sin el caso lejano


def test_el_radio_se_mide_desde_cada_caso_y_no_entre_todos_los_casos(session: Session) -> None:
    """Tres casos en línea a 0, 3 y 6 km: el del medio tiene a los otros dos dentro de 3,5 km, aunque los
    extremos estén a 6 km entre sí. Es el criterio de «casos dentro de la distancia» alrededor de un caso."""
    datos = escenario(session)
    ids = confirmados(
        session, datos, trips(session), [("5.2800", "-73.17"), ("5.3071", "-73.17"), ("5.3342", "-73.17")]
    )
    evaluar(session)
    [alerta] = alertas(session)
    assert casos_de(session, alerta["id_alerta"]) == set(ids)


def test_cambiar_la_distancia_del_parametro_cambia_el_resultado(session: Session) -> None:
    datos = escenario(session)
    confirmados(session, datos, trips(session), [CERCA[0], CERCA[1], A_3_7_KM])
    evaluar(session)
    assert alertas(session) == []
    session.execute(text("UPDATE parametros_epidemiologicos SET distancia_km = 4.00"))
    evaluar(session)
    assert len(alertas(session)) == 1


def test_cambiar_el_minimo_de_casos_cambia_el_resultado(session: Session) -> None:
    datos = escenario(session)
    confirmados(session, datos, trips(session), CERCA[:2])
    session.execute(text("UPDATE parametros_epidemiologicos SET numero_minimo_casos = 2"))
    evaluar(session)
    assert len(alertas(session)) == 1


# --- intervalo ---------------------------------------------------------------------------------------------------


def test_un_caso_diagnosticado_hace_29_dias_cuenta(session: Session) -> None:
    datos = escenario(session)
    confirmados(session, datos, trips(session), CERCA[:2])
    confirmados(session, datos, trips(session), CERCA[2:], dias_atras=29)
    evaluar(session)
    assert len(alertas(session)) == 1


def test_un_caso_diagnosticado_hace_31_dias_no_cuenta(session: Session) -> None:
    datos = escenario(session)
    confirmados(session, datos, trips(session), CERCA[:2])
    confirmados(session, datos, trips(session), CERCA[2:], dias_atras=31)
    evaluar(session)
    assert alertas(session) == []


def test_el_reloj_simulado_mueve_la_ventana(session: Session) -> None:
    datos = escenario(session)
    confirmados(session, datos, trips(session), CERCA)
    evaluar(session, fecha=AHORA + timedelta(days=40))  # 40 días después: ya salieron de la ventana de 30
    assert alertas(session) == []
    evaluar(session, fecha=AHORA + timedelta(days=5))
    assert len(alertas(session)) == 1


def test_un_diagnostico_posterior_a_la_fecha_evaluada_no_cuenta(session: Session) -> None:
    datos = escenario(session)
    confirmados(session, datos, trips(session), CERCA, dias_atras=-2)  # diagnosticados 2 días después de AHORA
    evaluar(session)
    assert alertas(session) == []


# --- qué casos cuentan -------------------------------------------------------------------------------------------


def test_otra_patologia_transmisible_confirmada_por_el_tecnico_cuenta(session: Session) -> None:
    datos = escenario(session)
    confirmados(session, datos, trips(session), CERCA, resultado="OTRA_PATOLOGIA")
    evaluar(session)
    assert len(alertas(session)) == 1


@pytest.mark.parametrize("resultado", ["NO_IDENTIFICADO", "REQUIERE_VISITA", None])
def test_los_diagnosticos_sin_categoria_no_cuentan(session: Session, resultado: str | None) -> None:
    datos = escenario(session)
    confirmados(session, datos, trips(session), CERCA[:2])
    confirmados(session, datos, None, CERCA[2:], resultado=resultado)  # type: ignore[arg-type]
    evaluar(session)
    assert alertas(session) == []


@pytest.mark.parametrize("nombre", ["Deficiencia de magnesio", "Botrytis", "Fusarium", "Roña"])
def test_las_categorias_no_transmisibles_nunca_generan_alerta(session: Session, nombre: str) -> None:
    datos = escenario(session)
    categoria = session.scalars(
        select(CategoriaFitosanitaria).where(CategoriaFitosanitaria.nombre_categoria == nombre)
    ).one()
    confirmados(session, datos, categoria, CERCA * 3)  # nueve casos
    evaluar(session)
    evaluar(session, categoria)  # tampoco si se pide expresamente
    assert alertas(session) == []


def test_una_categoria_transmisible_sin_parametros_no_genera_alerta(session: Session) -> None:
    datos = escenario(session)
    nueva = crear_categoria(session, "Transmisible sin parámetros", "S")
    confirmados(session, datos, nueva, CERCA)
    evaluar(session)
    assert alertas(session) == []


def test_las_categorias_no_se_mezclan(session: Session) -> None:
    datos = escenario(session)
    otra = crear_categoria(session, "Otra transmisible", "S")
    crear_parametro(session, otra)
    confirmados(session, datos, trips(session), CERCA[:2])
    confirmados(session, datos, otra, CERCA[2:])  # 2 de Trips + 1 de otra: ninguna llega a 3
    evaluar(session)
    assert alertas(session) == []


def test_cada_categoria_genera_su_propia_alerta(session: Session) -> None:
    datos = escenario(session)
    otra = crear_categoria(session, "Otra transmisible", "S")
    crear_parametro(session, otra, minimo=2, km="1.00", dias=10)
    confirmados(session, datos, trips(session), CERCA)
    confirmados(session, datos, otra, [("5.4000", "-73.0000"), ("5.4005", "-73.0000")])
    evaluar(session)
    por_categoria = {a["id_categoria"]: a for a in alertas(session)}
    assert set(por_categoria) == {trips(session).id_categoria, otra.id_categoria}
    assert por_categoria[otra.id_categoria]["intervalo_dias"] == 10  # sus propios parámetros


def test_pedir_una_categoria_solo_evalua_esa(session: Session) -> None:
    datos = escenario(session)
    otra = crear_categoria(session, "Otra transmisible", "S")
    crear_parametro(session, otra, minimo=2)
    confirmados(session, datos, trips(session), CERCA)
    confirmados(session, datos, otra, CERCA[:2])
    evaluar(session, otra)
    assert [a["id_categoria"] for a in alertas(session)] == [otra.id_categoria]


# --- idempotencia y evolución de una alerta ----------------------------------------------------------------------


def test_evaluar_dos_veces_no_duplica(session: Session) -> None:
    datos = escenario(session)
    confirmados(session, datos, trips(session), CERCA)
    evaluar(session)
    evaluar(session)
    assert len(alertas(session)) == 1
    assert len(casos_de(session, alertas(session)[0]["id_alerta"])) == 3


def test_un_cuarto_caso_se_suma_a_la_alerta_abierta(session: Session) -> None:
    datos = escenario(session)
    ids = confirmados(session, datos, trips(session), CERCA)
    evaluar(session)
    ids += confirmados(session, datos, trips(session), [("5.2830", "-73.1700")], dias_atras=0)
    evaluar(session)
    [alerta] = alertas(session)  # sigue siendo una sola alerta
    assert casos_de(session, alerta["id_alerta"]) == set(ids)


def test_tambien_se_suma_a_una_alerta_en_revision(session: Session) -> None:
    datos = escenario(session)
    ids = confirmados(session, datos, trips(session), CERCA)
    evaluar(session)
    session.execute(text("UPDATE alertas SET estado_alerta = 'EN_REVISION'"))
    ids += confirmados(session, datos, trips(session), [("5.2830", "-73.1700")], dias_atras=0)
    evaluar(session)
    assert len(alertas(session)) == 1 and len(casos_de(session, alertas(session)[0]["id_alerta"])) == 4
    assert ids  # los cuatro casos están vinculados


@pytest.mark.parametrize("estado", ["CONFIRMADA", "COMUNICADA"])
def test_tras_una_alerta_atendida_un_caso_nuevo_genera_otra(session: Session, estado: str) -> None:
    datos = escenario(session)
    viejos = confirmados(session, datos, trips(session), CERCA)
    evaluar(session)
    session.execute(text("UPDATE alertas SET estado_alerta = :e"), {"e": estado})
    nuevo = confirmados(session, datos, trips(session), [("5.2830", "-73.1700")], dias_atras=0)
    evaluar(session)
    primera, segunda = alertas(session)
    assert primera["estado_alerta"] == estado and segunda["estado_alerta"] == "GENERADA"
    assert casos_de(session, segunda["id_alerta"]) == set(viejos + nuevo)  # la nueva alerta suma los casos cercanos


@pytest.mark.parametrize("estado", ["DESCARTADA", "CORREGIDA"])
def test_una_alerta_descartada_o_corregida_no_se_regenera(session: Session, estado: str) -> None:
    datos = escenario(session)
    confirmados(session, datos, trips(session), CERCA)
    evaluar(session)
    session.execute(text("UPDATE alertas SET estado_alerta = :e"), {"e": estado})
    evaluar(session)
    evaluar(session, fecha=AHORA + timedelta(days=1))
    assert len(alertas(session)) == 1


def test_el_barrido_diario_sin_categoria_recupera_lo_que_se_pasó_por_alto(session: Session) -> None:
    """P-B01: además de evaluar tras cada diagnóstico, un barrido de respaldo cubre las alertas que faltaron."""
    datos = escenario(session)
    confirmados(session, datos, trips(session), CERCA)  # nadie llamó a sp_evaluar_alerta
    assert alertas(session) == []
    evaluar(session, None)
    assert len(alertas(session)) == 1


def test_dos_grupos_lejanos_generan_dos_alertas(session: Session) -> None:
    datos = escenario(session)
    norte = [("5.5000", "-73.1700"), ("5.5010", "-73.1700"), ("5.5020", "-73.1700")]
    confirmados(session, datos, trips(session), CERCA)
    confirmados(session, datos, trips(session), norte)
    evaluar(session)
    primera, segunda = alertas(session)
    assert casos_de(session, primera["id_alerta"]).isdisjoint(casos_de(session, segunda["id_alerta"]))


def test_no_se_crea_la_alerta_sin_el_minimo_aunque_haya_alertas_viejas(session: Session) -> None:
    datos = escenario(session)
    confirmados(session, datos, trips(session), CERCA[:2], dias_atras=60)
    evaluar(session)
    assert alertas(session) == []
