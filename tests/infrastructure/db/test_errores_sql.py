"""Códigos de error de los objetos SQL y sentencias de llamada: sin base de datos."""

from types import SimpleNamespace

import pytest

from app.infrastructure.db import procedimientos as sp
from app.infrastructure.db.procedimientos import SqlState, error_de_negocio, sqlstate_de


def test_los_cinco_codigos_propios() -> None:
    assert {e.name: e.value for e in SqlState} == {
        "TRANSICION_INVALIDA": "AN001",
        "NO_ES_EL_TECNICO_ASIGNADO": "AN002",
        "DATOS_INVALIDOS": "AN003",
        "NO_ENCONTRADO": "AN004",
        "AUDITORIA_INMUTABLE": "AN005",
    }


def test_ningun_codigo_choca_con_un_sqlstate_estandar() -> None:
    """Las clases estándar de PostgreSQL usan solo dígitos en las dos primeras posiciones; 'AN' queda libre."""
    assert all(e.value[:2] == "AN" for e in SqlState)


def test_sqlstate_de_una_excepcion_de_sqlalchemy() -> None:
    error = SimpleNamespace(orig=SimpleNamespace(sqlstate="AN001"))
    assert sqlstate_de(error) == "AN001"  # type: ignore[arg-type]


def test_sqlstate_de_una_excepcion_del_driver() -> None:
    assert sqlstate_de(SimpleNamespace(sqlstate="23505")) == "23505"  # type: ignore[arg-type]


def test_sqlstate_de_otra_excepcion() -> None:
    assert sqlstate_de(ValueError("no es de la base")) is None


def test_error_de_negocio_reconoce_los_codigos_propios() -> None:
    error = SimpleNamespace(orig=SimpleNamespace(sqlstate="AN002"))
    assert error_de_negocio(error) is SqlState.NO_ES_EL_TECNICO_ASIGNADO  # type: ignore[arg-type]


@pytest.mark.parametrize("sqlstate", ["23505", "42P01", "AN999", ""])
def test_error_de_negocio_ignora_los_demas(sqlstate: str) -> None:
    assert error_de_negocio(SimpleNamespace(sqlstate=sqlstate)) is None  # type: ignore[arg-type]


def test_error_de_negocio_ignora_excepciones_ajenas() -> None:
    assert error_de_negocio(RuntimeError("x")) is None


SENTENCIAS = {
    "REGISTRAR_EVENTO": "sp_registrar_evento",
    "REGISTRAR_AUDITORIA": "sp_registrar_auditoria",
    "REGISTRAR_DIAGNOSTICO": "sp_registrar_diagnostico",
    "CERRAR_CASO": "sp_cerrar_caso",
    "MARCAR_ADVERTENCIA_ENVIADA": "sp_marcar_advertencia_enviada",
    "EVALUAR_ALERTA": "sp_evaluar_alerta",
}


@pytest.mark.parametrize(("constante", "procedimiento"), SENTENCIAS.items())
def test_cada_sentencia_llama_a_su_procedimiento(constante: str, procedimiento: str) -> None:
    assert str(getattr(sp, constante)).startswith(f"CALL {procedimiento}(")


def test_las_sentencias_tipan_sus_parametros() -> None:
    """Sin CAST, Postgres no deduce el tipo de un parámetro None y la llamada ambigua falla."""
    for constante in SENTENCIAS:
        sql = str(getattr(sp, constante))
        for nombre in getattr(sp, constante)._bindparams:
            assert f"CAST(:{nombre} AS" in sql, (constante, nombre)


def test_el_contexto_de_la_transaccion_es_local() -> None:
    assert "true)" in str(sp.ESTABLECER_ACTOR)  # set_config(..., true): dura solo la transacción
    assert "anuc.actor_tipo" in str(sp.ESTABLECER_ACTOR) and "anuc.ahora" in str(sp.ESTABLECER_AHORA)


def test_parametros_de_diagnostico() -> None:
    assert sp.parametros_diagnostico("CAS-1", "TEC-1", "NO_IDENTIFICADO") == {
        "id_caso": "CAS-1",
        "id_tecnico": "TEC-1",
        "resultado": "NO_IDENTIFICADO",
        "id_categoria": None,
        "motivo_visita": None,
        "descripcion": None,
        "fecha": None,
    }
