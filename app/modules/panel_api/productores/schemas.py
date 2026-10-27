from datetime import datetime

from pydantic import BaseModel, Field

from app.modules.panel_api.cuentas.schemas import Celular, Nombre


class ProductorIn(BaseModel):
    nombre: Nombre
    celular: Celular
    activo: bool = True


class ProductorEditar(BaseModel):
    """`PATCH /productores/{id}`: nombre y celular. El estado tiene su propio endpoint."""

    nombre: Nombre | None = None
    celular: Celular | None = None


class FincaDeProductorOut(BaseModel):
    id: str = Field(examples=["FIN-000001"])
    nombre: str
    vereda: str = Field(examples=["JURACAMBITA"])
    especies: list[str] = Field(examples=[["GULUPA"]])


class ProductorOut(BaseModel):
    id: str = Field(examples=["PRO-000001"])
    nombre: str
    celular: str = Field(pattern=r"^3\d{9}$", examples=["3001234567"])
    activo: bool
    vereda: str | None = Field(description="Vereda de su primera finca; nula si aún no tiene fincas")
    fincas: list[FincaDeProductorOut]
    registrado_en: datetime
    ultima_consulta_en: datetime | None = Field(description="Último mensaje que envió por WhatsApp")
    casos_ultimo_anio: int
    casos_activos: int = Field(description="Casos Pendiente de revisión, En revisión o Registrado")
