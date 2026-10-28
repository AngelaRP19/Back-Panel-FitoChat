"""Forma de los reportes: KPI + secciones genéricas (barras, tablas y datos), la que ya dibuja el panel (pantalla 24).

Los nombres de los campos son los del frontend (`porPagina` va en camelCase); el resto de la API usa snake_case.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Tono = Literal["info", "ok", "adv", "revision", "peligro", "neutro"]
Celda = str | int | float


class KpiReporte(BaseModel):
    rotulo: str
    valor: str
    pie: str
    tono: Tono


class ItemBarra(BaseModel):
    etiqueta: str
    valor: float
    texto: str | None = Field(None, description="Texto de la cifra («1,5 días»); por defecto, el valor")
    categoria: str | None = Field(None, description="Código de la categoría: el panel dibuja su forma")


class _SeccionBase(BaseModel):
    id: str
    titulo: str
    subtitulo: str | None = None
    ancho: Literal["mitad", "completo"]
    nota: str | None = None


class SeccionBarras(_SeccionBase):
    tipo: Literal["barras"] = "barras"
    items: list[ItemBarra]


class ColumnaTabla(BaseModel):
    clave: str
    titulo: str
    alinear: Literal["derecha"] | None = None


class SeccionTabla(_SeccionBase):
    model_config = ConfigDict(populate_by_name=True)

    tipo: Literal["tabla"] = "tabla"
    columnas: list[ColumnaTabla]
    filas: list[dict[str, Celda]] = Field(description="`_alerta` trae el id de la alerta de la fila")
    por_pagina: int | None = Field(None, alias="porPagina")
    sustantivo: str | None = None
    vacio: str | None = None


class ItemDato(BaseModel):
    termino: str
    valor: str


class SeccionDatos(_SeccionBase):
    tipo: Literal["datos"] = "datos"
    items: list[ItemDato]


Seccion = Annotated[SeccionBarras | SeccionTabla | SeccionDatos, Field(discriminator="tipo")]


TipoReporte = Literal["casos", "identificacion", "alertas", "productores", "tecnicos"]


class ReporteDatos(BaseModel):
    tipo: TipoReporte
    titulo: str
    subtitulo: str
    total: int = Field(description="Elementos que coinciden con los filtros (casos, alertas, productores o cuentas)")
    sustantivo: str
    kpis: list[KpiReporte]
    secciones: list[Seccion]


class TarjetaReporte(BaseModel):
    tipo: TipoReporte
    titulo: str
    descripcion: str
    destacados: list[str]


class ResumenReportes(BaseModel):
    subtitulo: str
    total: int
    sustantivo: str
    kpis: list[KpiReporte]
    reportes: list[TarjetaReporte]
    secciones: list[Seccion]


class Documento(BaseModel):
    """Lo que se exporta: el reporte con su encabezado y los filtros aplicados."""

    reporte: ReporteDatos
    periodo_largo: str
    periodo_corto: str
    chips: list[str]
    generado: str
    nombre_archivo: str


Visibilidad = Literal["yo", "equipo"]


class ConsultaGuardada(BaseModel):
    """Consulta guardada (D-19). Sin envío programado (D-21): `envio` siempre es nulo."""

    id: str = Field(examples=["CNS-3FA9C1D2E4"])
    nombre: str
    tipo: TipoReporte
    descripcion: str
    filtros: dict[str, str]
    resultado: str = Field(description="Resultado actual («6 casos»), recalculado al pedir la lista")
    visibilidad: Visibilidad
    envio: None = None
    creada_por: str = Field(description="Nombre del líder que la guardó")


class DatosConsulta(BaseModel):
    nombre: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=60)]
    tipo: TipoReporte
    filtros: dict[str, str] = Field(default_factory=dict)
    visibilidad: Visibilidad = "equipo"
    descripcion: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None
    envio: object | None = Field(None, description="Se ignora: no hay envío programado (D-21)")
