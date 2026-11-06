from datetime import date, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field

from app.core.catalogos_api import EstadoAlerta, ResultadoTecnico, normalizar_codigo
from app.core.guardrail import MENSAJE_BLOQUEADO, recomienda_tratamiento
from app.modules.panel_api.casos.schemas import PersonaRef

MAXIMO_MENSAJE = 300
MAXIMO_JUSTIFICACION = 500


def _mensaje(valor: str) -> str:
    valor = valor.strip()
    if not valor:
        raise ValueError("Escribe la recomendación para los productores.")
    if len(valor) > MAXIMO_MENSAJE:
        raise ValueError(f"Máximo {MAXIMO_MENSAJE} caracteres.")
    if recomienda_tratamiento(valor):
        raise ValueError(MENSAJE_BLOQUEADO)
    return valor


def _justificacion(valor: str) -> str:
    valor = valor.strip()
    if not valor:
        raise ValueError("Escribe el motivo del descarte.")
    if len(valor) > MAXIMO_JUSTIFICACION:
        raise ValueError(f"Máximo {MAXIMO_JUSTIFICACION} caracteres.")
    return valor


def _veredas(valor: list[str]) -> list[str]:
    codigos = list(dict.fromkeys(c for c in (normalizar_codigo(v) for v in valor) if c))
    if not codigos:
        raise ValueError("Elige al menos una vereda con productores activos.")
    return codigos


Mensaje = Annotated[str, AfterValidator(_mensaje)]


class BorradorIn(BaseModel):
    mensaje: Mensaje


class ComunicarIn(BaseModel):
    mensaje: Mensaje
    veredas: Annotated[list[str], AfterValidator(_veredas)] = Field(
        description="Códigos de vereda, por ejemplo SAN_LUIS"
    )


class CorregirIn(BaseModel):
    mensaje: Mensaje = Field(description="Explicación del líder; se agrega a la plantilla fija de corrección")


class DescartarIn(BaseModel):
    justificacion: Annotated[str, AfterValidator(_justificacion)]


class ParametrosIn(BaseModel):
    num_casos: int = Field(ge=2, le=999, description="Casos mínimos para generar la alerta")
    distancia_km: float = Field(gt=0, le=50, description="Radio en kilómetros")
    intervalo_dias: int = Field(ge=1, le=365, description="Ventana de días entre los diagnósticos")


class ParametrosAlerta(BaseModel):
    num_casos: int
    distancia_km: float
    intervalo_dias: int


class ParametroCategoria(BaseModel):
    categoria: str
    nombre: str
    habilitada: bool = Field(description="Participa en las alertas: solo las categorías transmisibles")
    num_casos: int | None
    distancia_km: float | None
    intervalo_dias: int | None
    nota: str | None
    actualizado_por: PersonaRef | None = Field(description="Sale de la auditoría; nulo si nunca se editó")
    actualizado_en: datetime | None


class AlertaResumen(BaseModel):
    id: str = Field(examples=["ALR-000012"])
    codigo: str = Field(examples=["ALR-012"])
    estado: EstadoAlerta
    categoria: str
    vereda: str | None = Field(description="Vereda donde más casos se concentran")
    generada_en: datetime
    num_casos: int
    radio_km: float = Field(description="Distancia máxima real entre las fincas de los casos")
    parametros: ParametrosAlerta = Field(description="Con los que se generó; no cambian si luego se editan")
    revisa: PersonaRef | None
    confirmada_por: PersonaRef | None
    productores_notificados: int | None
    envios_fallidos: int | None
    motivo_descarte: str | None


class ConteosAlertas(BaseModel):
    TODAS: int
    GENERADA: int = 0
    EN_REVISION: int = 0
    CONFIRMADA: int = 0
    DESCARTADA: int = 0
    COMUNICADA: int = 0
    CORREGIDA: int = 0


class PaginaAlertas(BaseModel):
    items: list[AlertaResumen]
    total: int
    page: int
    page_size: int
    conteos: ConteosAlertas


class CasoAlerta(BaseModel):
    id: str
    codigo: str
    productor_nombre: str | None = Field(description="Oculto al técnico en los casos que no tiene asignados")
    finca_nombre: str
    resultado_tecnico: ResultadoTecnico
    enviado_en: datetime | None
    latitud: float | None
    longitud: float | None
    enlazable: bool = Field(description="Quien consulta puede abrir el caso (el técnico, solo los suyos)")


class Descarte(BaseModel):
    motivo: str
    por: PersonaRef | None
    en: datetime | None


class Borrador(BaseModel):
    mensaje: str
    veredas: list[str] = Field(
        description="Veredas elegidas. Hoy no se guardan: se devuelve la vereda de la alerta (pendiente de modelo)"
    )
    guardado_en: datetime | None = Field(description="Hoy no se guarda la fecha del borrador (pendiente de modelo)")


class Correccion(BaseModel):
    mensaje: str
    por: PersonaRef | None
    en: datetime | None


class AlertaDetalle(AlertaResumen):
    casos: list[CasoAlerta]
    sin_diagnostico: list[str] = Field(
        description="Códigos (#0230) de casos de la misma categoría en la zona aún sin diagnóstico: no cuentan"
    )
    confirmada_en: datetime | None
    comunicada_en: date | None
    descarte: Descarte | None
    borrador: Borrador | None
    mensaje_enviado: str | None
    correccion: Correccion | None
    siguiendo: bool


class ProductorDestino(BaseModel):
    id: str
    nombre: str
    celular: str = Field(pattern=r"^3\d{9}$")


class DestinatariosVereda(BaseModel):
    vereda: str
    nombre: str
    zona_alerta: bool = Field(description="Es una de las veredas donde están los casos")
    deshabilitados: int = Field(description="Productores deshabilitados: no reciben mensajes")
    productores: list[ProductorDestino]


class SeguirOut(BaseModel):
    siguiendo: bool
