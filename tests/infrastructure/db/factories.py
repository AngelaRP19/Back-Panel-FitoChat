"""Constructores mínimos de filas válidas para los tests de base de datos."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.infrastructure.db.models import (
    Cultivo,
    CultivoEspecie,
    Especie,
    Estado,
    Finca,
    IdentificacionPreliminar,
    Productor,
)


@dataclass
class Escenario:
    productor: Productor
    finca: Finca
    cultivo: Cultivo
    especie: Especie
    otra_especie: Especie
    estado: Estado
    identificacion: IdentificacionPreliminar


def ahora() -> datetime:
    return datetime.now(UTC)


def escenario(session: Session) -> Escenario:
    """Productor -> finca -> cultivo con UNA especie asociada (y otra especie sin asociar)."""
    productor = Productor(
        nombre_productor="Productor de prueba",
        telefono_productor="573001112233",
        estado_cuenta_productor="ACTIVO",
        fecha_registro_productor=date(2026, 10, 1),
    )
    finca = Finca(
        nombre_finca="La Esperanza",
        vereda_sector_finca="Centro",
        latitud_finca=Decimal("5.283200"),
        longitud_finca=Decimal("-73.171000"),
        productor=productor,
    )
    cultivo = Cultivo(
        nombre_cultivo="Lote 1",
        latitud_cultivo=Decimal("5.284000"),
        longitud_cultivo=Decimal("-73.170500"),
        finca=finca,
    )
    especie = Especie(nombre_especie="Especie test A")
    otra = Especie(nombre_especie="Especie test B")
    cultivo.especies_asociadas.append(CultivoEspecie(especie=especie))
    estado = Estado(nombre_estado="Estado test", descripcion_estado="Solo para pruebas")
    identificacion = IdentificacionPreliminar(
        tipo_resultado="IDENTIFICADO", explicacion_resultado="Prueba", fecha_analisis=ahora()
    )
    session.add_all([productor, otra, estado, identificacion])
    session.flush()
    return Escenario(productor, finca, cultivo, especie, otra, estado, identificacion)
