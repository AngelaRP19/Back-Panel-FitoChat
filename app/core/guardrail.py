"""Filtro de mensajes: el sistema no receta tratamientos, dosis ni productos (guía §2, RQF45).

Se aplica a todo texto que el líder redacta para los productores. Es una red de seguridad de palabras y cantidades,
no un análisis del sentido: prefiere bloquear de más y pedir que se reescriba.
"""

import re
import unicodedata

MENSAJE_BLOQUEADO = "El mensaje no puede recomendar productos, dosis ni tratamientos."

_PATRONES = [
    re.compile(p)
    for p in (
        r"\b(dosis|dosific\w*|concentracion\w*|dilu\w+|mezcl\w+)\b",
        r"\b\d+(?:[.,]\d+)?\s*(?:ml|cc|l|lt|lts|litros?|g|gr|grs|gramos?|kg|kilos?|mg|cm3|onzas?|libras?)\b",
        r"\b(aplic\w+|fumig\w+|asperj\w+|rocie\w*|roci\w+|bombe\w+|desinfect\w+)\b",
        r"\b(insecticidas?|fungicidas?|pesticidas?|plaguicidas?|acaricidas?|herbicidas?|agroquimic\w+|quimic\w+)\b",
        r"\b(veneno\w*|toxic\w+|fertiliz\w+|abono\w*|caldo|jabon\w*|cal|azufre|cobre)\b",
        r"\b(tratamientos?|tratar\w*|productos?|medicament\w+|remedios?|curar|cura)\b",
    )
]


# La identificación es siempre preliminar y se expresa como «porcentaje de coincidencia de síntomas» (guía §2):
# nunca «confianza», «probabilidad» ni «certeza».
_TERMINOS_PROHIBIDOS = re.compile(r"\b(confianza|probabilidad(?:es)?|probable(?:s|mente)?|certeza|seguro de que)\b")
MENSAJE_TERMINOS = "El texto usa un término que no corresponde a una identificación preliminar."


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")


def recomienda_tratamiento(texto: str) -> bool:
    """True si el texto parece recomendar un producto, una dosis o un tratamiento."""
    limpio = _sin_tildes(texto)
    return any(patron.search(limpio) for patron in _PATRONES)


def usa_terminos_prohibidos(texto: str) -> bool:
    """True si el texto habla de «confianza», «probabilidad» o «certeza» en lugar de un porcentaje de coincidencia."""
    return _TERMINOS_PROHIBIDOS.search(_sin_tildes(texto)) is not None


def es_seguro(texto: str) -> bool:
    """Un texto que puede llegar al productor: ni receta tratamientos ni promete certeza."""
    return not recomienda_tratamiento(texto) and not usa_terminos_prohibidos(texto)
