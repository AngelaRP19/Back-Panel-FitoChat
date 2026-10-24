"""Importa todos los modelos para registrarlos en Base.metadata (lo usan Alembic y los relationship)."""

from app.infrastructure.db.models.arquitectura import ConocimientoEmbedding, MensajeWhatsapp
from app.infrastructure.db.models.auditoria import Auditoria
from app.infrastructure.db.models.casos import Caso, Evento
from app.infrastructure.db.models.catalogos import CategoriaFitosanitaria, Especie, Estado
from app.infrastructure.db.models.cuentas import LiderTecnico, Tecnico
from app.infrastructure.db.models.diagnostico import Diagnostico, Fotografia, Pregunta, Respuesta
from app.infrastructure.db.models.epidemiologia import Alerta, ParametroEpidemiologico
from app.infrastructure.db.models.identificacion import CoincidenciaCategoria, IdentificacionPreliminar
from app.infrastructure.db.models.territorio import Cultivo, CultivoEspecie, Finca, Productor

__all__ = [
    "Alerta",
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
