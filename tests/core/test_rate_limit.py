import pytest

from app.core.rate_limit import LimitadorVentana


class Reloj:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def reloj() -> Reloj:
    return Reloj()


def test_permite_hasta_el_maximo_y_rechaza_el_siguiente(reloj: Reloj) -> None:
    limitador = LimitadorVentana(maximo=5, ventana_segundos=60, reloj=reloj)
    assert [limitador.permitir("1.1.1.1") for _ in range(6)] == [True] * 5 + [False]


def test_cada_clave_tiene_su_propia_cuenta(reloj: Reloj) -> None:
    limitador = LimitadorVentana(maximo=2, ventana_segundos=60, reloj=reloj)
    assert limitador.permitir("a") and limitador.permitir("a") and not limitador.permitir("a")
    assert limitador.permitir("b")  # otra IP no se ve afectada


def test_la_ventana_se_desliza(reloj: Reloj) -> None:
    limitador = LimitadorVentana(maximo=2, ventana_segundos=60, reloj=reloj)
    limitador.permitir("a")
    reloj.t += 30
    limitador.permitir("a")
    reloj.t += 31  # el primero ya salió de la ventana; el segundo todavía no
    assert limitador.permitir("a")
    assert not limitador.permitir("a")


def test_pasada_la_ventana_se_puede_volver_a_intentar(reloj: Reloj) -> None:
    limitador = LimitadorVentana(maximo=1, ventana_segundos=60, reloj=reloj)
    assert limitador.permitir("a") and not limitador.permitir("a")
    reloj.t += 60
    assert limitador.permitir("a")


def test_un_intento_rechazado_no_extiende_el_bloqueo(reloj: Reloj) -> None:
    limitador = LimitadorVentana(maximo=1, ventana_segundos=60, reloj=reloj)
    limitador.permitir("a")
    for _ in range(5):
        reloj.t += 10
        assert not limitador.permitir("a")  # insistir no alarga la espera
    reloj.t += 10
    assert limitador.permitir("a")  # 60 s después del único intento aceptado


def test_segundos_para_reintentar(reloj: Reloj) -> None:
    limitador = LimitadorVentana(maximo=1, ventana_segundos=60, reloj=reloj)
    assert limitador.segundos_para_reintentar("a") == 0  # sin bloqueo
    limitador.permitir("a")
    assert 60 <= limitador.segundos_para_reintentar("a") <= 61
    reloj.t += 45
    assert 15 <= limitador.segundos_para_reintentar("a") <= 16
    reloj.t += 20
    assert limitador.segundos_para_reintentar("a") == 0


def test_reiniciar_borra_todo(reloj: Reloj) -> None:
    limitador = LimitadorVentana(maximo=1, ventana_segundos=60, reloj=reloj)
    limitador.permitir("a")
    limitador.reiniciar()
    assert limitador.permitir("a")


def test_la_memoria_no_crece_sin_limite(reloj: Reloj) -> None:
    limitador = LimitadorVentana(maximo=5, ventana_segundos=60, reloj=reloj)
    for i in range(1500):
        limitador.permitir(f"ip-{i}")
    reloj.t += 120  # todas vencieron
    limitador.permitir("nueva")
    assert len(limitador._eventos) < 1500  # se descartaron las claves sin actividad reciente
