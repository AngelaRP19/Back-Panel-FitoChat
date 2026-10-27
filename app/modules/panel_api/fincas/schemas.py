import math
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field

from app.core.catalogos_api import EspecieCultivo, TipoCultivo, normalizar_codigo
from app.modules.panel_api.cuentas.schemas import Nombre

# El diseño sugiere la zona de Zetaquirá; fuera de ella solo se advierte, no se bloquea (guía §11, módulo 3).
LATITUD_ESPERADA = (5.20, 5.30)


def _vereda(valor: str) -> str:
    valor = " ".join(valor.split())
    if not normalizar_codigo(valor):
        raise ValueError("Escribe la vereda.")
    if len(valor) > 100:
        raise ValueError("La vereda no puede pasar de 100 caracteres.")
    return valor


def _coordenada(minimo: float, maximo: float, mensaje: str) -> AfterValidator:
    def validar(valor: float) -> float:
        if not math.isfinite(valor) or not minimo <= valor <= maximo:
            raise ValueError(mensaje)
        return valor

    return AfterValidator(validar)


Vereda = Annotated[str, AfterValidator(_vereda), Field(examples=["Juracambita"])]
Latitud = Annotated[float, _coordenada(-90, 90, "La latitud va entre -90 y 90 grados, con punto decimal.")]
Longitud = Annotated[float, _coordenada(-180, 180, "La longitud va entre -180 y 180 grados, con punto decimal.")]


def _sin_repetidas(valor: list[EspecieCultivo]) -> list[EspecieCultivo]:
    return list(dict.fromkeys(valor))


Especies = Annotated[list[EspecieCultivo], Field(min_length=1), AfterValidator(_sin_repetidas)]


class FincaIn(BaseModel):
    nombre: Nombre
    vereda: Vereda
    latitud: Latitud
    longitud: Longitud
    productor_id: str = Field(examples=["PRO-000001"])


class CultivoEnFincaEditar(BaseModel):
    """Cultivo de la finca que se edita junto con ella (el formulario del panel guarda todo en un PATCH)."""

    id: str = Field(examples=["CUL-000001"])
    tipo: TipoCultivo | None = None
    especies: Especies | None = None


class FincaEditar(BaseModel):
    nombre: Nombre | None = None
    vereda: Vereda | None = None
    latitud: Latitud | None = None
    longitud: Longitud | None = None
    cultivos: list[CultivoEnFincaEditar] | None = None


class CultivoIn(BaseModel):
    nombre: Nombre
    tipo: TipoCultivo
    especies: Especies


class CultivoEditar(BaseModel):
    nombre: Nombre | None = None
    tipo: TipoCultivo | None = None
    especies: Especies | None = None


class ProductorDeFincaOut(BaseModel):
    id: str
    nombre: str
    activo: bool


class CultivoOut(BaseModel):
    id: str = Field(examples=["CUL-000001"])
    nombre: str
    tipo: TipoCultivo
    especies: list[str] = Field(examples=[["GULUPA", "MARACUYA"]])
    casos_activos: list[str] = Field(description="Ids de los casos activos del cultivo")


class FincaOut(BaseModel):
    id: str = Field(examples=["FIN-000001"])
    nombre: str
    vereda: str = Field(examples=["JURACAMBITA"])
    latitud: float
    longitud: float
    productor: ProductorDeFincaOut
    cultivos: list[CultivoOut]
    casos_activos: int
    advertencias: list[str] = Field(description="Avisos que no bloquean, por ejemplo coordenadas fuera de la zona")


class CodigoNombre(BaseModel):
    codigo: str
    nombre: str


class CategoriaOut(CodigoNombre):
    es_transmisible: bool
