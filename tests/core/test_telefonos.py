import pytest

from app.core.telefonos import celular_a_e164, celular_de_e164, es_celular_valido, normalizar_celular


@pytest.mark.parametrize("celular", ["3001234567", "3999999999"])
def test_celulares_validos(celular: str) -> None:
    assert es_celular_valido(celular)


@pytest.mark.parametrize(
    "celular", ["", "300123456", "30012345678", "2001234567", "573001234567", "300 123 4567", "30012345a7"]
)
def test_celulares_invalidos(celular: str) -> None:
    assert not es_celular_valido(celular)
    with pytest.raises(ValueError):
        celular_a_e164(celular)


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("+573001234567", "3001234567"),
        ("573001234567", "3001234567"),
        ("300 123 4567", "3001234567"),
        ("300-123-4567", "3001234567"),
        ("3001234567", "3001234567"),
        ("12345", "12345"),
    ],
)
def test_normalizar_celular_acepta_lo_que_envia_el_panel(texto: str, esperado: str) -> None:
    assert normalizar_celular(texto) == esperado


def test_ida_y_vuelta_e164() -> None:
    assert celular_a_e164("3001234567") == "573001234567"
    assert celular_de_e164("573001234567") == "3001234567"


@pytest.mark.parametrize("valor", ["3001234567", "5730012", "+573001234567", "abc"])
def test_celular_de_e164_deja_igual_lo_que_no_es_e164(valor: str) -> None:
    assert celular_de_e164(valor) == valor
