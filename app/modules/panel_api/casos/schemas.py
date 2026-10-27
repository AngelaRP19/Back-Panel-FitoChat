from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.core.catalogos_api import (
    EspecieCultivo,
    EstadoCaso,
    ResultadoAnalisis,
    ResultadoTecnico,
    TipoCultivo,
    TipoEvento,
    TipoEvidencia,
)

CodigoCategoria = Literal["BOTRYTIS", "RONA", "DEFICIENCIA_MAGNESIO", "TRIPS", "FUSARIUM"]


class AsignarIn(BaseModel):
    tecnico_id: str = Field(examples=["TEC-000001"])


class PersonaRef(BaseModel):
    id: str
    nombre: str


class ProductorCaso(BaseModel):
    id: str
    nombre: str
    celular: str = Field(pattern=r"^3\d{9}$")


class FincaCaso(BaseModel):
    id: str
    nombre: str
    vereda: str = Field(examples=["SAN_LUIS"])


class CultivoCaso(BaseModel):
    id: str
    nombre: str


class Alternativa(BaseModel):
    categoria: str
    porcentaje: float


class Preliminar(BaseModel):
    """Identificación preliminar: nunca es un diagnóstico. El porcentaje es de coincidencia de síntomas."""

    resultado: ResultadoAnalisis
    categoria: str | None = Field(description="Nula si ninguna categoría llegó al umbral")
    porcentaje: float | None = None
    alternativa: Alternativa | None = Field(default=None, description="Solo si el resultado es ambiguo")


class CasoResumen(BaseModel):
    id: str = Field(examples=["CAS-00000241"])
    codigo: str = Field(examples=["#0241"])
    estado: EstadoCaso
    productor: ProductorCaso
    finca: FincaCaso
    cultivo: CultivoCaso
    especie: EspecieCultivo | str
    preliminar: Preliminar | None = Field(description="Nulo mientras el chatbot no termina el análisis")
    tecnico: PersonaRef | None = Field(description="Responsable: técnico de la asignación vigente")
    enviado_a_panel_en: datetime | None = Field(description="Nulo: «Sin enviar»")
    vence_en: date | None = Field(description="Fin de la conservación; se fija al cerrar el caso")


class ConteosCasos(BaseModel):
    todos: int
    CREADO: int = 0
    EN_ANALISIS: int = 0
    PENDIENTE_REVISION: int = 0
    EN_REVISION: int = 0
    REGISTRADO: int = 0
    CERRADO: int = 0
    DESCARTADO: int = 0


# --- Detalle -------------------------------------------------------------------------------------------------------


class ProductorDetalle(ProductorCaso):
    casos_ultimo_anio: int


class FincaDetalle(FincaCaso):
    latitud: float | None
    longitud: float | None


class CultivoDetalle(CultivoCaso):
    tipo: TipoCultivo | str
    especies: list[str]


class PorcentajeCategoria(BaseModel):
    categoria: str
    porcentaje: float


class CoincidenciaTexto(BaseModel):
    categoria: str
    texto: str


class EvidenciasResumen(BaseModel):
    total: int = Field(description="Fotos útiles")
    maximo: int = Field(description="Máximo de fotos útiles por caso")
    por_tipo: dict[str, int]


class CuestionarioResumen(BaseModel):
    respuestas: int
    desde: datetime | None
    hasta: datetime | None


class HistorialUltimo(BaseModel):
    titulo: str
    en: datetime


class DiagnosticoOut(BaseModel):
    resultado_tecnico: ResultadoTecnico
    categoria_determinada: str | None
    motivo_visita: str | None
    descripcion: str
    observaciones: str
    tecnico_responsable: PersonaRef | None
    registrado_en: datetime


class CasoDetalle(BaseModel):
    id: str = Field(examples=["CAS-00000241"])
    codigo: str = Field(examples=["#0241"])
    estado: EstadoCaso
    productor: ProductorDetalle
    finca: FincaDetalle
    cultivo: CultivoDetalle
    especie: EspecieCultivo | str
    zona_afectada: str
    preliminar: Preliminar | None
    preliminar_porcentajes: list[PorcentajeCategoria] = Field(description="Porcentaje de coincidencia por categoría")
    preliminar_resumen: str | None
    preliminar_coincidencias: list[CoincidenciaTexto] = Field(description="Resultado ambiguo: en qué coincide cada una")
    tecnico: PersonaRef | None
    enviado_a_panel_en: datetime | None
    vence_en: date | None
    cerrado_en: datetime | None
    evidencias_resumen: EvidenciasResumen
    cuestionario_resumen: CuestionarioResumen
    historial_ultimo: HistorialUltimo | None
    diagnostico: DiagnosticoOut | None
    puede_diagnosticar: bool = Field(description="Falso para el líder: no diagnostica con su rol (D-02)")
    puede_cerrar: bool
    puede_asignar: bool


# --- Partes del caso -----------------------------------------------------------------------------------------------


class EvidenciaOut(BaseModel):
    id: str = Field(examples=["FOT-00000001"])
    tipo: TipoEvidencia
    numero: int = Field(description="Número dentro de su tipo (Fruto 2)")
    util: bool
    descripcion: str = Field(description="Lo que respondió el productor junto con la foto")
    pregunta: str
    recibida_en: datetime
    url: str | None = Field(description="URL prefirmada de lectura; nula si el almacenamiento no está configurado")
    url_expira_en: datetime | None


class RespuestaOut(BaseModel):
    pregunta: str
    respuesta: str


class CuestionarioOut(BaseModel):
    desde: datetime | None
    hasta: datetime | None
    respuestas: list[RespuestaOut]


class ResponsableEvento(BaseModel):
    tipo: str = Field(examples=["LIDER"])
    id: str | None
    nombre: str


class EventoOut(BaseModel):
    tipo: TipoEvento | str
    titulo: str
    en: datetime
    detalle: str
    responsable: ResponsableEvento | None


class DescartaOut(BaseModel):
    categoria: str
    texto: str


class ExplicacionOut(BaseModel):
    coincide: list[str] = Field(description="Síntomas que coinciden con la categoría propuesta")
    descarta: list[DescartaOut] = Field(description="Por qué se descartan las demás")
    cercanas: list[PorcentajeCategoria] = Field(
        description="Categorías por debajo del umbral, de la más cercana a la más lejana"
    )
    texto: str | None
    regla: str = Field(description="Regla aplicada: identificado, ambiguo o no identificado")


class NavegacionOut(BaseModel):
    pendientes: int = Field(description="Líder: casos sin técnico. Técnico: sus casos en revisión")
    alertas_generadas: int = Field(description="Alertas GENERADA por revisar (solo las gestiona el líder)")
