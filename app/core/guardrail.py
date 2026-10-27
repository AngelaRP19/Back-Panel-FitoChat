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


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")


def recomienda_tratamiento(texto: str) -> bool:
    """True si el texto parece recomendar un producto, una dosis o un tratamiento."""
    limpio = _sin_tildes(texto)
    return any(patron.search(limpio) for patron in _PATRONES)
