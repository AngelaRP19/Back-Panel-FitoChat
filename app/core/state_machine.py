"""Transiciones válidas del caso (guía §2). Cualquier otra se rechaza con `409 TRANSICION_INVALIDA`.

Creado → En análisis → Pendiente de revisión → En revisión → Registrado → Cerrado; y Creado o En análisis → Descartado.
Los casos NO se reabren. Reasignar un caso En revisión a otro técnico no cambia el estado (no es una transición).
"""

from app.core.catalogos_api import EstadoCaso

E = EstadoCaso

TRANSICIONES: dict[EstadoCaso, frozenset[EstadoCaso]] = {
    E.CREADO: frozenset({E.EN_ANALISIS, E.DESCARTADO}),
    E.EN_ANALISIS: frozenset({E.PENDIENTE_REVISION, E.DESCARTADO}),
    E.PENDIENTE_REVISION: frozenset({E.EN_REVISION}),
    E.EN_REVISION: frozenset({E.REGISTRADO}),
    E.REGISTRADO: frozenset({E.CERRADO}),
    E.CERRADO: frozenset(),
    E.DESCARTADO: frozenset(),
}

# Estados en los que el líder puede asignar o reasignar el técnico: antes de que exista diagnóstico.
ESTADOS_ASIGNABLES: frozenset[EstadoCaso] = frozenset({E.PENDIENTE_REVISION, E.EN_REVISION})


class TransicionInvalida(Exception):
    def __init__(self, origen: EstadoCaso, destino: EstadoCaso) -> None:
        super().__init__(f"No se puede pasar de {origen.value} a {destino.value}")
        self.origen = origen
        self.destino = destino


def puede_pasar(origen: EstadoCaso, destino: EstadoCaso) -> bool:
    return destino in TRANSICIONES[origen]


def validar_transicion(origen: EstadoCaso, destino: EstadoCaso) -> None:
    if not puede_pasar(origen, destino):
        raise TransicionInvalida(origen, destino)


def es_asignable(estado: EstadoCaso) -> bool:
    return estado in ESTADOS_ASIGNABLES
