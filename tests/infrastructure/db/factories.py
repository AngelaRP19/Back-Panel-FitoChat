"""Constructores mínimos de filas válidas para los tests de base de datos."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.infrastructure.db.models import (
    Alerta,
    Caso,
    CategoriaFitosanitaria,
    Cultivo,
    CultivoEspecie,
    Especie,
    Estado,
    Finca,
    IdentificacionPreliminar,
    LiderTecnico,
    Productor,
    Tecnico,
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
        tipo_cultivo="CAMPO_ABIERTO",
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


# --- Cuentas, casos y alertas -----------------------------------------------------------------------------------


def crear_tecnico(session: Session, numero: int = 1) -> Tecnico:
    tecnico = Tecnico(
        nombre_tecnico=f"Técnico {numero}",
        correo_tecnico=f"tecnico{numero}@prueba.test",
        celular_tecnico=f"57310000{numero:04d}",
        contrasena_tecnico="hash-inutilizable",
        estado_cuenta_tecnico="ACTIVO",
        fecha_registro_tecnico=date(2026, 10, 1),
    )
    session.add(tecnico)
    session.flush()
    return tecnico


def crear_lider(session: Session, numero: int = 1) -> LiderTecnico:
    lider = LiderTecnico(
        nombre_lider_tecnico=f"Líder {numero}",
        correo_lider_tecnico=f"lider{numero}@prueba.test",
        celular_lider_tecnico=f"57320000{numero:04d}",
        contrasena_lider_tecnico="hash-inutilizable",
        estado_cuenta_lider_tecnico="ACTIVO",
    )
    session.add(lider)
    session.flush()
    return lider


def crear_caso(session: Session, datos: Escenario, *, con_identificacion: bool = True) -> Caso:
    caso = Caso(
        id_cultivo=datos.cultivo.id_cultivo,
        id_especie=datos.especie.id_especie,
        id_estado=datos.estado.id_estado,
        id_identificacion_preliminar=datos.identificacion.id_identificacion if con_identificacion else None,
        fecha_creacion_caso=ahora(),
    )
    session.add(caso)
    session.flush()
    return caso


def crear_categoria(
    session: Session, nombre: str = "Categoría de prueba", transmisible: str = "S"
) -> CategoriaFitosanitaria:
    categoria = CategoriaFitosanitaria(nombre_categoria=nombre, es_transmisible=transmisible)
    session.add(categoria)
    session.flush()
    return categoria


def crear_alerta(session: Session, categoria: CategoriaFitosanitaria, **extra: object) -> Alerta:
    campos: dict[str, object] = {
        "id_categoria": categoria.id_categoria,
        "estado_alerta": "GENERADA",
        "fecha_generacion_alerta": ahora(),
        "numero_minimo_casos": 3,
        "distancia_km": Decimal("3.50"),
        "intervalo_dias": 30,
    }
    campos.update(extra)
    alerta = Alerta(**campos)
    session.add(alerta)
    session.flush()
    return alerta


def viola(session: Session, constraint: str, accion: Callable[[], None]) -> None:
    """Ejecuta la acción en un savepoint y comprueba que falla por la restricción indicada."""
    with pytest.raises(IntegrityError) as exc:
        with session.begin_nested():
            accion()
            session.flush()
    assert exc.value.orig.diag.constraint_name == constraint  # type: ignore[union-attr]


def viola_not_null(session: Session, columna: str, accion: Callable[[], None]) -> None:
    """Igual que `viola`, para una columna obligatoria (SQLSTATE 23502)."""
    with pytest.raises(IntegrityError) as exc:
        with session.begin_nested():
            accion()
            session.flush()
    assert exc.value.orig.sqlstate == "23502"  # type: ignore[union-attr]
    assert exc.value.orig.diag.column_name == columna  # type: ignore[union-attr]
