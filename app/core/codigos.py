"""Códigos de presentación (guía §6.1): se derivan del id y solo sirven para mostrar."""

_MINIMO_DIGITOS_CASO = 4


def numero_de_id(id_con_prefijo: str) -> int:
    """'CAS-00000241' -> 241."""
    return int(id_con_prefijo.rsplit("-", 1)[-1])


def codigo_caso(id_caso: str) -> str:
    """'CAS-00000241' -> '#0241' (mínimo 4 dígitos)."""
    return f"#{numero_de_id(id_caso):0{_MINIMO_DIGITOS_CASO}d}"


def codigo_alerta(id_alerta: str) -> str:
    """'ALR-000012' -> 'ALR-012' (3 dígitos)."""
    return f"ALR-{numero_de_id(id_alerta):03d}"
