"""Cuando la IA no responde: se reintenta, se pide perdón y la conversación sigue con las fotos."""

from app.infrastructure.ai_client import AiSimulado, ContextoConsulta, ErrorIA, Turno
from app.modules.orchestrator import textos as t
from tests.modules.orchestrator.conftest import Sistema, productor_con_finca

PREGUNTA = "¿Cuándo notaste las manchas?"


class IaCaida(AiSimulado):
    """Falla las primeras `fallos` llamadas a `conducir` y después responde con los turnos programados."""

    def __init__(self, fallos: int) -> None:
        super().__init__()
        self.fallos = fallos
        self.intentos = 0

    def conducir(self, contexto: ContextoConsulta, mensaje: str | None) -> Turno:
        self.intentos += 1
        if self.fallos > 0:
            self.fallos -= 1
            raise ErrorIA("caída")
        return super().conducir(contexto, mensaje)


def con_ia(sistema: Sistema, ia: AiSimulado) -> list[float]:
    """Sustituye la IA del orquestador y devuelve la lista donde se anotan las pausas entre reintentos."""
    pausas: list[float] = []
    sistema.orquestador.ai = ia
    sistema.orquestador.dormir = pausas.append
    return pausas


def abrir_caso(sistema: Sistema) -> list[str]:
    productor_con_finca(sistema.motor)
    sistema.escribir("hola")
    return sistema.escribir("1")


def test_un_fallo_pasajero_se_reintenta_y_el_productor_no_se_entera(sistema: Sistema) -> None:
    ia = IaCaida(fallos=1)
    ia.turnos.append(Turno(pregunta=PREGUNTA))
    pausas = con_ia(sistema, ia)
    respuestas = abrir_caso(sistema)
    assert respuestas[-1] == PREGUNTA and t.IA_NO_DISPONIBLE not in respuestas
    assert ia.intentos == 2 and pausas == [sistema.orquestador.ajustes.ia_pausa_reintento_segundos]


def test_si_la_ia_no_responde_se_pide_perdon_y_se_pasa_a_las_fotos(sistema: Sistema) -> None:
    ia = IaCaida(fallos=99)
    con_ia(sistema, ia)
    respuestas = abrir_caso(sistema)
    assert t.IA_NO_DISPONIBLE in respuestas and respuestas[-1].startswith("Ahora envíame fotos")
    assert sistema.paso() == "FOTOS" and ia.intentos == sistema.orquestador.ajustes.ia_reintentos


def test_un_caso_sin_cuestionario_por_falla_de_la_ia_igual_llega_al_tecnico(sistema: Sistema) -> None:
    con_ia(sistema, IaCaida(fallos=99))
    abrir_caso(sistema)
    sistema.foto("m1", "hoja")
    sistema.escribir("listo")
    assert sistema.escribir("sí")[0] == t.ENVIADO and sistema.estado_caso() == "Pendiente de revisión"


def test_la_falla_a_mitad_del_cuestionario_conserva_lo_ya_respondido(sistema: Sistema) -> None:
    ia = IaCaida(fallos=0)
    ia.turnos.extend([Turno(pregunta=PREGUNTA), Turno(pregunta="¿Dónde?")])
    con_ia(sistema, ia)
    abrir_caso(sistema)
    sistema.escribir("hace una semana")  # se guarda y la IA pregunta lo siguiente
    ia.fallos = 99  # y ahora se cae
    respuestas = sistema.escribir("en las hojas")
    assert respuestas[0] == t.IA_NO_DISPONIBLE and sistema.paso() == "FOTOS"
    guardadas = sistema.consulta("SELECT texto_respuesta AS r FROM respuestas")
    assert [g["r"] for g in guardadas] == ["hace una semana"]


def test_el_orquestador_no_oculta_errores_que_no_son_de_la_ia(sistema: Sistema) -> None:
    class Defectuosa(AiSimulado):
        def conducir(self, contexto: ContextoConsulta, mensaje: str | None) -> Turno:
            raise KeyError("bug")

    con_ia(sistema, Defectuosa())
    productor_con_finca(sistema.motor)
    sistema.escribir("hola")
    try:
        sistema.escribir("1")
    except Exception as error:  # el consumidor reintentará el mensaje: es un fallo nuestro, no de la IA
        assert isinstance(error, KeyError)
    else:
        raise AssertionError("debió propagar el error")
    assert sistema.consulta("SELECT 1 FROM casos") == []  # y la transacción del mensaje no dejó nada
