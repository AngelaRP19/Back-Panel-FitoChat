import pytest

from app.core.codigos import codigo_alerta, codigo_caso, numero_de_id


@pytest.mark.parametrize(
    ("id_caso", "codigo"),
    [("CAS-00000241", "#0241"), ("CAS-00000001", "#0001"), ("CAS-00001234", "#1234"), ("CAS-00012345", "#12345")],
)
def test_codigo_del_caso_tiene_minimo_cuatro_digitos(id_caso: str, codigo: str) -> None:
    assert codigo_caso(id_caso) == codigo


@pytest.mark.parametrize(
    ("id_alerta", "codigo"), [("ALR-000012", "ALR-012"), ("ALR-000001", "ALR-001"), ("ALR-001234", "ALR-1234")]
)
def test_codigo_de_la_alerta(id_alerta: str, codigo: str) -> None:
    assert codigo_alerta(id_alerta) == codigo


def test_numero_de_id() -> None:
    assert numero_de_id("PRO-000042") == 42


def test_un_id_sin_numero_falla() -> None:
    with pytest.raises(ValueError):
        codigo_caso("CAS-")
