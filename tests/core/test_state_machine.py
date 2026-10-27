import pytest

from app.core.catalogos_api import EstadoCaso
from app.core.state_machine import (
    ESTADOS_ASIGNABLES,
    TRANSICIONES,
    TransicionInvalida,
    es_asignable,
    puede_pasar,
    validar_transicion,
)

E = EstadoCaso
VALIDAS = [
    (E.CREADO, E.EN_ANALISIS),
    (E.CREADO, E.DESCARTADO),
    (E.EN_ANALISIS, E.PENDIENTE_REVISION),
    (E.EN_ANALISIS, E.DESCARTADO),
    (E.PENDIENTE_REVISION, E.EN_REVISION),
    (E.EN_REVISION, E.REGISTRADO),
    (E.REGISTRADO, E.CERRADO),
]


def test_cada_estado_tiene_sus_transiciones() -> None:
    assert set(TRANSICIONES) == set(EstadoCaso)


@pytest.mark.parametrize(("origen", "destino"), VALIDAS)
def test_transiciones_validas(origen: EstadoCaso, destino: EstadoCaso) -> None:
    assert puede_pasar(origen, destino)
    validar_transicion(origen, destino)


def test_todo_lo_demas_se_rechaza() -> None:
    for origen in EstadoCaso:
        for destino in EstadoCaso:
            if (origen, destino) not in VALIDAS:
                assert not puede_pasar(origen, destino), (origen, destino)
                with pytest.raises(TransicionInvalida):
                    validar_transicion(origen, destino)


@pytest.mark.parametrize("estado", [E.CERRADO, E.DESCARTADO])
def test_los_estados_finales_no_salen_de_ahi_ni_se_reabren(estado: EstadoCaso) -> None:
    assert TRANSICIONES[estado] == frozenset()
    for destino in EstadoCaso:
        assert not puede_pasar(estado, destino)


def test_un_caso_no_vuelve_atras() -> None:
    assert not puede_pasar(E.EN_REVISION, E.PENDIENTE_REVISION)
    assert not puede_pasar(E.REGISTRADO, E.EN_REVISION)
    assert not puede_pasar(E.CERRADO, E.REGISTRADO)


def test_el_error_nombra_los_estados() -> None:
    with pytest.raises(TransicionInvalida) as exc:
        validar_transicion(E.CERRADO, E.EN_REVISION)
    assert exc.value.origen is E.CERRADO and exc.value.destino is E.EN_REVISION
    assert "CERRADO" in str(exc.value)


def test_solo_se_asigna_antes_del_diagnostico() -> None:
    assert ESTADOS_ASIGNABLES == {E.PENDIENTE_REVISION, E.EN_REVISION}
    assert [e for e in EstadoCaso if es_asignable(e)] == [E.PENDIENTE_REVISION, E.EN_REVISION]
