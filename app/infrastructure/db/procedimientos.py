"""Llamadas a los procedimientos y funciones SQL del Avance 5 (migración 0003) y sus códigos de error.

Las sentencias son `text()` con parámetros con nombre y tipos explícitos, así que sirven igual con una `Connection`
síncrona (pruebas, Alembic, jobs) que con una `AsyncSession` (la API). Detalle en docs/OBJETOS_SQL.md.
"""

from enum import StrEnum
from typing import Any

from sqlalchemy import TextClause, text

ESQUEMA_ACTOR = "anuc.actor_tipo"
ESQUEMA_ACTOR_ID = "anuc.actor_id"
ESQUEMA_AHORA = "anuc.ahora"


class SqlState(StrEnum):
    """SQLSTATE propios con los que los objetos SQL avisan de un error de negocio."""

    TRANSICION_INVALIDA = "AN001"  # el caso no está en el estado que exige la operación
    NO_ES_EL_TECNICO_ASIGNADO = "AN002"  # quien actúa no es el técnico (activo) de la asignación vigente
    DATOS_INVALIDOS = "AN003"
    NO_ENCONTRADO = "AN004"
    AUDITORIA_INMUTABLE = "AN005"


def sqlstate_de(error: BaseException) -> str | None:
    """SQLSTATE de una excepción de SQLAlchemy o del driver; None si no trae."""
    original = getattr(error, "orig", error)
    return getattr(original, "sqlstate", None)


def error_de_negocio(error: BaseException) -> SqlState | None:
    """El error de negocio que representa la excepción, o None si es otro tipo de fallo."""
    codigo = sqlstate_de(error)
    try:
        return SqlState(codigo) if codigo else None
    except ValueError:
        return None


# --- Contexto de la transacción -----------------------------------------------------------------------------------

# Quién actúa: los triggers lo anotan en `eventos`. Sin él, el actor es SISTEMA. Dura lo que la transacción.
ESTABLECER_ACTOR: TextClause = text(
    f"SELECT set_config('{ESQUEMA_ACTOR}', :tipo, true), set_config('{ESQUEMA_ACTOR_ID}', :id, true)"
)
# Reloj simulado (pruebas y datos de demostración).
ESTABLECER_AHORA: TextClause = text(f"SELECT set_config('{ESQUEMA_AHORA}', :ahora, true)")

# --- Funciones ----------------------------------------------------------------------------------------------------

ID_ESTADO: TextClause = text("SELECT fn_id_estado(CAST(:nombre AS varchar))")

# --- Procedimientos -----------------------------------------------------------------------------------------------

REGISTRAR_EVENTO: TextClause = text(
    "CALL sp_registrar_evento(CAST(:id_caso AS varchar), CAST(:tipo_evento AS varchar), "
    "CAST(:descripcion AS varchar), CAST(:actor_tipo AS varchar), CAST(:actor_id AS varchar), "
    "COALESCE(CAST(:fecha AS timestamptz), fn_ahora()))"
)
REGISTRAR_AUDITORIA: TextClause = text(
    "CALL sp_registrar_auditoria(CAST(:origen AS varchar), CAST(:id_actor AS varchar), CAST(:accion AS varchar), "
    "CAST(:entidad AS varchar), CAST(:descripcion AS varchar), CAST(:valor_anterior AS varchar), "
    "CAST(:valor_nuevo AS varchar), CAST(:resultado AS varchar), COALESCE(CAST(:fecha AS timestamptz), fn_ahora()))"
)
REGISTRAR_DIAGNOSTICO: TextClause = text(
    "CALL sp_registrar_diagnostico(CAST(:id_caso AS varchar), CAST(:id_tecnico AS varchar), "
    "CAST(:resultado AS varchar), CAST(:id_categoria AS varchar), CAST(:motivo_visita AS varchar), "
    "CAST(:descripcion AS varchar), COALESCE(CAST(:fecha AS timestamptz), fn_ahora()))"
)
CERRAR_CASO: TextClause = text(
    "CALL sp_cerrar_caso(CAST(:id_caso AS varchar), CAST(:actor_tipo AS varchar), CAST(:actor_id AS varchar), "
    "CAST(:meses AS integer), COALESCE(CAST(:fecha AS timestamptz), fn_ahora()))"
)
MARCAR_ADVERTENCIA_ENVIADA: TextClause = text("CALL sp_marcar_advertencia_enviada(CAST(:id_caso AS varchar))")
EVALUAR_ALERTA: TextClause = text(
    "CALL sp_evaluar_alerta(CAST(:id_categoria AS varchar), COALESCE(CAST(:fecha AS timestamptz), fn_ahora()))"
)


def parametros_diagnostico(
    id_caso: str,
    id_tecnico: str,
    resultado: str,
    *,
    id_categoria: str | None = None,
    motivo_visita: str | None = None,
    descripcion: str | None = None,
    fecha: Any = None,
) -> dict[str, Any]:
    return {
        "id_caso": id_caso,
        "id_tecnico": id_tecnico,
        "resultado": resultado,
        "id_categoria": id_categoria,
        "motivo_visita": motivo_visita,
        "descripcion": descripcion,
        "fecha": fecha,
    }
