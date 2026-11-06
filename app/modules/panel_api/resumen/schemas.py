from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.core.catalogos_api import EstadoAlerta, EstadoCaso, ResultadoTecnico

Periodo = Literal["30d", "90d", "anio"]


class CerradosMes(BaseModel):
    total: int
    confirmados: int
    otra_patologia: int
    no_identificado: int
    requiere_visita: int


class KpisLider(BaseModel):
    pendientes_revision: int = Field(description="Sin técnico asignado")
    pendientes_mas_48h: int = Field(description="Pendientes con más de 48 h desde que llegaron al panel")
    en_revision: int = Field(description="Con técnico asignado y sin diagnóstico")
    cerrados_mes: CerradosMes = Field(description="Cerrados en el mes en curso (hora de Colombia), por resultado")
    proximos_a_vencer: int = Field(description="Cerrados cuya conservación vence dentro de `dias_aviso_vencimiento`")
    dias_aviso_vencimiento: int


class CasoPendiente(BaseModel):
    id: str
    codigo: str
    numero: int = Field(description="Número del caso (241 en CAS-00000241)")
    productor_nombre: str
    enviado_a_panel_en: datetime
    tecnico_asignado_id: str | None


class AlertaReciente(BaseModel):
    id: str
    codigo: str
    estado: EstadoAlerta
    categoria: str
    vereda: str | None
    num_casos: int
    productores_notificados: int | None = Field(description="Solo cuando ya se comunicó")
    generada_en: datetime


class AlertaDestacada(BaseModel):
    categoria: str
    vereda: str | None


class ResumenAlertas(BaseModel):
    generadas_sin_revisar: int
    destacada: AlertaDestacada | None = Field(description="La alerta GENERADA más reciente")
    recientes: list[AlertaReciente]


class CargaTecnico(BaseModel):
    tecnico_id: str
    nombre: str
    activos: int = Field(description="En revisión y registrados sin cerrar")
    en_revision: int


class InicioLider(BaseModel):
    rol: Literal["LIDER_TECNICO"] = "LIDER_TECNICO"
    generado_en: datetime
    kpis: KpisLider
    pendientes_antiguos: list[CasoPendiente] = Field(description="Sin diagnóstico, del envío más antiguo al más nuevo")
    pendientes_sin_tecnico: int
    alertas: ResumenAlertas
    carga_tecnicos: list[CargaTecnico] = Field(description="Técnicos activos, de más a menos carga")


class KpisTecnico(BaseModel):
    en_revision: int
    en_revision_mas_48h: int
    registrados_sin_cerrar: list[str] = Field(description="Ids de sus casos con diagnóstico guardado y sin cerrar")
    cerrados_mes: CerradosMes
    proximos_a_vencer: int
    dias_aviso_vencimiento: int


class PorAtender(BaseModel):
    id: str
    codigo: str
    productor_nombre: str
    estado: EstadoCaso
    enviado_a_panel_en: datetime | None
    mas_48h: bool = Field(description="Lleva más de 48 h en espera")


class DiagnosticoReciente(BaseModel):
    caso_id: str
    codigo: str
    resultado_tecnico: ResultadoTecnico
    categoria: str | None
    registrado_en: datetime


class InicioTecnico(BaseModel):
    rol: Literal["TECNICO"] = "TECNICO"
    generado_en: datetime
    nombre: str
    lider_nombre: str | None = Field(description="Líder activo, para pedirle un caso que no aparece")
    kpis: KpisTecnico
    por_atender: list[PorAtender] = Field(description="En revisión (los que más esperan primero) y luego registrados")
    diagnosticos_recientes: list[DiagnosticoReciente]


Inicio = Annotated[InicioLider | InicioTecnico, Field(discriminator="rol")]


class CategoriaTotal(BaseModel):
    categoria: str
    total: int


class CasosPorCategoria(BaseModel):
    periodo: Periodo
    total: int
    items: list[CategoriaTotal]
