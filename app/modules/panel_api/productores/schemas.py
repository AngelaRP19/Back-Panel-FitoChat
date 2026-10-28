from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field

from app.core.guardrail import MENSAJE_BLOQUEADO, recomienda_tratamiento
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


# --- Acciones masivas (pantalla 21 · B-06) ---------------------------------------------------------------------------

MAXIMO_SELECCION = 500
MAXIMO_MENSAJE_MASIVO = 500


def _ids(valor: list[str]) -> list[str]:
    ids = list(dict.fromkeys(i.strip() for i in valor if i.strip()))
    if not ids:
        raise ValueError("Elige al menos un productor.")
    if len(ids) > MAXIMO_SELECCION:
        raise ValueError(f"Elige máximo {MAXIMO_SELECCION} productores a la vez.")
    return ids


def _mensaje_masivo(valor: str) -> str:
    valor = valor.strip()
    if not valor:
        raise ValueError("Escribe el mensaje para los productores.")
    if len(valor) > MAXIMO_MENSAJE_MASIVO:
        raise ValueError(f"Máximo {MAXIMO_MENSAJE_MASIVO} caracteres.")
    if recomienda_tratamiento(valor):
        raise ValueError(MENSAJE_BLOQUEADO)
    return valor


Seleccion = Annotated[list[str], AfterValidator(_ids)]


class EstadoMasivoIn(BaseModel):
    ids: Seleccion
    activo: bool


class EstadoMasivoOut(BaseModel):
    actualizados: int = Field(description="Los que cambiaron (los que ya estaban así no cuentan)")
    solicitados: int


class MensajeMasivoIn(BaseModel):
    ids: Seleccion
    mensaje: Annotated[str, AfterValidator(_mensaje_masivo)]


class MensajeMasivoOut(BaseModel):
    enviados: int
    omitidos: int = Field(description="Deshabilitados, inexistentes o con envío fallido")
