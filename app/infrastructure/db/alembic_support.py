from typing import Any

from geoalchemy2 import alembic_helpers

# Tablas que crean las extensiones (PostGIS/topology); autogenerate no debe tocarlas.
_EXTENSION_TABLES = {"spatial_ref_sys", "topology", "layer"}


def include_object(obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any) -> bool:
    """Filtro de autogenerate: ignora tablas ajenas a los modelos y delega en geoalchemy2."""
    if type_ == "table" and (name in _EXTENSION_TABLES or (reflected and compare_to is None)):
        return False
    return bool(alembic_helpers.include_object(obj, name, type_, reflected, compare_to))  # type: ignore[no-untyped-call]
