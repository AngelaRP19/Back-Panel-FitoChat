"""Filtros del reporte de alertas (panel F2 del frontend) y cálculo del periodo."""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from app.api.errors import ApiError
from app.core.catalogos_api import EstadoAlerta

Periodo = Literal["7d", "30d", "mes", "anio", "personalizado"]
Comunicada = Literal["si", "sin_comunicar"]
PERIODO_PREDETERMINADO: Periodo = "anio"
MAXIMO_DIAS = 366  # 12 meses, como el panel


@dataclass(frozen=True)
class FiltrosAlertas:
    periodo: Periodo | None = None
    desde: date | None = None
    hasta: date | None = None
    vereda: str | None = None  # código de la API («SAN_LUIS»)
    estados: list[str] = field(default_factory=list)  # códigos de EstadoAlerta
    decidida: str | None = None  # «sin» o el id del líder que decidió
    casos_min: int | None = None
    comunicada: Comunicada | None = None


def estados_de(texto: str | None) -> list[str]:
    """Lista separada por comas («GENERADA,CONFIRMADA»); un código que no existe es un error de validación."""
    pedidos = [e.strip() for e in (texto or "").split(",") if e.strip()]
    validos = {e.value for e in EstadoAlerta}
    desconocidos = [e for e in pedidos if e not in validos]
    if desconocidos:
        raise ApiError(
            422,
            "VALIDACION",
            "Hay estados de alerta que no existen.",
            {"estado_alerta": f"Desconocidos: {', '.join(desconocidos)}"},
        )
    return pedidos


def rango_del_periodo(f: FiltrosAlertas, hoy: date) -> tuple[date, date, Periodo]:
    """Fechas del periodo (ambas incluidas). Sin periodo: «personalizado» si hay fechas y, si no, el año en curso."""
    periodo = f.periodo or ("personalizado" if f.desde or f.hasta else PERIODO_PREDETERMINADO)
    if periodo == "7d":
        desde, hasta = hoy - timedelta(days=6), hoy
    elif periodo == "30d":
        desde, hasta = hoy - timedelta(days=29), hoy
    elif periodo == "mes":
        desde, hasta = hoy.replace(day=1), hoy
    elif periodo == "anio":
        desde, hasta = date(hoy.year, 1, 1), hoy
    else:
        desde, hasta = f.desde or date(hoy.year, 1, 1), f.hasta or hoy
    if desde > hasta:
        raise ApiError(
            422, "VALIDACION", "La fecha inicial no puede ser posterior a la final.", {"desde": "Posterior a «hasta»"}
        )
    if (hasta - desde).days > MAXIMO_DIAS:
        raise ApiError(422, "VALIDACION", "El rango máximo es de 12 meses.", {"hasta": "Máximo 12 meses."})
    return desde, hasta, periodo
