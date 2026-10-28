"""Lo que un manejador pide hacer FUERA de la base, y que solo se ejecuta después de confirmar la transacción.

Si la transacción se deshace, las respuestas no salen y el estado de la conversación no cambia. Así un reintento no
duplica mensajes al productor ni deja el estado de la conversación por delante de la base de datos.
"""

from dataclasses import dataclass, field

from app.infrastructure.conversaciones_store import Conversacion
from app.modules.panel_api.notificaciones.service import Aviso


@dataclass
class Efectos:
    respuestas: list[str] = field(default_factory=list)  # textos para el mismo teléfono que escribió
    conversacion: Conversacion | None = None  # estado nuevo que guardar
    borrar_conversacion: bool = False  # la conversación terminó: se borra su estado
    avisos: list[Aviso] = field(default_factory=list)  # notificaciones de la campana del panel (B-10)
