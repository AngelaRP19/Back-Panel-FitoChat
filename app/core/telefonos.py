"""Celulares colombianos: 10 dígitos en la API (`^3\\d{9}$`) y E.164 (`57...`) en base de datos y WhatsApp."""

import re

PATRON_CELULAR = re.compile(r"^3\d{9}$")
_INDICATIVO = "57"


def es_celular_valido(celular: str) -> bool:
    return PATRON_CELULAR.fullmatch(celular) is not None


def celular_a_e164(celular: str) -> str:
    """'3001234567' -> '573001234567'."""
    if not es_celular_valido(celular):
        raise ValueError("El celular debe tener 10 dígitos y empezar por 3")
    return _INDICATIVO + celular


def celular_de_e164(e164: str) -> str:
    """'573001234567' -> '3001234567'. Un valor que no sea E.164 colombiano se devuelve sin cambios."""
    if len(e164) == 12 and e164.startswith(_INDICATIVO) and e164[2:].isdigit():
        return e164[2:]
    return e164
