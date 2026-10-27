"""Lectura de lo que escribe el productor: sí/no, opciones por número o por nombre y limpieza de texto."""

import pytest

from app.modules.orchestrator.orquestador import elegir, lista_numerada, normalizar, si_o_no

OPCIONES = [("FIN-1", "La Esperanza"), ("FIN-2", "El Roble"), ("FIN-3", "El Rosal")]


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Sí", True),
        ("SI.", True),
        ("¡claro!", True),
        ("  ok ", True),
        ("No", False),
        ("nO.", False),
        ("tal vez", None),
        ("", None),
        ("sí no", None),
    ],
)
def test_si_o_no(texto: str, esperado: bool | None) -> None:
    assert si_o_no(texto) is esperado


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("1", "FIN-1"),
        (" 2 ", "FIN-2"),
        ("3.", "FIN-3"),
        ("la esperanza", "FIN-1"),
        ("EL ROBLE", "FIN-2"),
        ("Esperanza", None),
    ],
)
def test_elegir_por_numero_o_nombre(texto: str, esperado: str | None) -> None:
    opcion = elegir(texto, OPCIONES)
    assert (opcion[0] if opcion else None) == esperado


@pytest.mark.parametrize("texto", ["0", "4", "99", "", "el", "otra"])
def test_elegir_rechaza_lo_que_no_es_inequivoco(texto: str) -> None:
    """Fuera de rango, vacío o ambiguo («el» coincide con dos opciones)."""
    assert elegir(texto, OPCIONES) is None


def test_un_comienzo_unico_basta() -> None:
    assert elegir("el roble", OPCIONES) == ("FIN-2", "El Roble")
    assert elegir("la", OPCIONES) == ("FIN-1", "La Esperanza")  # único que empieza así


def test_normalizar_quita_tildes_mayusculas_y_signos() -> None:
    assert normalizar("  ¡SÍ, Señor! ") == "si senor" and normalizar("/SALIR") == "/salir"


def test_lista_numerada() -> None:
    assert lista_numerada(OPCIONES) == "1. La Esperanza\n2. El Roble\n3. El Rosal"
