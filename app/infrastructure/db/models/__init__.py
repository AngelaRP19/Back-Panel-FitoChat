"""Importa todos los modelos para registrarlos en Base.metadata (lo usan Alembic y los relationship).

También registra el generador de ids (`before_flush`): quien use los modelos sin pasar por `session.py` (el
consumidor de la cola, los jobs) necesita que los objetos nuevos reciban su id con prefijo.
"""

import app.infrastructure.db.ids  # noqa: F401  (registra el generador de ids en before_flush)
from app.infrastructure.db.models.arquitectura import ConocimientoEmbedding, MensajeWhatsapp
from app.infrastructure.db.models.auditoria import Auditoria
from app.infrastructure.db.models.casos import Asignacion, Caso, Evento
from app.infrastructure.db.models.catalogos import CategoriaFitosanitaria, Especie, Estado
from app.infrastructure.db.models.cuentas import LiderTecnico, Tecnico
from app.infrastructure.db.models.diagnostico import Diagnostico, Fotografia, Pregunta, Respuesta
from app.infrastructure.db.models.epidemiologia import Alerta, AlertaCaso, AlertaDestinatario, ParametroEpidemiologico
from app.infrastructure.db.models.identificacion import CoincidenciaCategoria, IdentificacionPreliminar
from app.infrastructure.db.models.territorio import Cultivo, CultivoEspecie, Finca, Productor

__all__ = [
    "Alerta",
    "AlertaCaso",
    "AlertaDestinatario",
    "Asignacion",
    "Auditoria",
    "Caso",
    "CategoriaFitosanitaria",
    "CoincidenciaCategoria",
    "ConocimientoEmbedding",
    "Cultivo",
    "CultivoEspecie",
    "Diagnostico",
    "Especie",
    "Estado",
    "Evento",
    "Finca",
    "Fotografia",
    "IdentificacionPreliminar",
    "LiderTecnico",
    "MensajeWhatsapp",
    "ParametroEpidemiologico",
    "Pregunta",
    "Productor",
    "Respuesta",
    "Tecnico",
]
