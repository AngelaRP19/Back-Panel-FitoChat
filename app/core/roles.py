from enum import StrEnum


class Rol(StrEnum):
    """Roles del panel (guía §6.2). Cada uno vive en su propia tabla de cuentas."""

    LIDER_TECNICO = "LIDER_TECNICO"  # tabla lideres_tecnicos, ids LID-
    TECNICO = "TECNICO"  # tabla tecnicos, ids TEC-
