"""Constructores mínimos de filas válidas para los tests de base de datos."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import count

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.infrastructure.db import procedimientos as sp
from app.infrastructure.db.models import (
    Alerta,
    Asignacion,
    Caso,
    CategoriaFitosanitaria,
    Cultivo,
    CultivoEspecie,
    Diagnostico,
    Especie,
    Estado,
    Finca,
    IdentificacionPreliminar,
    LiderTecnico,
    ParametroEpidemiologico,
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


# --- Objetos SQL (migración 0003) -------------------------------------------------------------------------------

_secuencia_focos = count(1)


def id_estado(session: Session, nombre: str) -> str:
    return str(session.scalar(sp.ID_ESTADO, {"nombre": nombre}))


def llamar(session: Session, sentencia: object, **parametros: object) -> None:
    session.execute(sentencia, parametros)  # type: ignore[call-overload]


def falla_con(session: Session, codigo: sp.SqlState, accion: Callable[[], object]) -> None:
    """La acción debe fallar con ese SQLSTATE de negocio; se revierte a un savepoint para seguir usando la sesión."""
    with pytest.raises(DBAPIError) as exc:
        with session.begin_nested():
            accion()
    assert sp.sqlstate_de(exc.value) == codigo.value, exc.value


def caso_en(session: Session, datos: Escenario, estado: str, *, con_identificacion: bool = True) -> Caso:
    """Caso del escenario en el estado indicado (nombre real, de las semillas)."""
    caso = Caso(
        id_cultivo=datos.cultivo.id_cultivo,
        id_especie=datos.especie.id_especie,
        id_estado=id_estado(session, estado),
        id_identificacion_preliminar=datos.identificacion.id_identificacion if con_identificacion else None,
        fecha_creacion_caso=ahora(),
    )
    session.add(caso)
    session.flush()
    return caso


def asignar(session: Session, caso: Caso, tecnico: Tecnico, *, vigente: bool = True) -> Asignacion:
    asignacion = Asignacion(
        id_caso=caso.id_caso,
        id_tecnico=tecnico.id_tecnico,
        fecha_asignacion=ahora(),
        fecha_fin=None if vigente else ahora(),
    )
    session.add(asignacion)
    session.flush()
    return asignacion


def caso_en_revision(session: Session, datos: Escenario, tecnico: Tecnico) -> Caso:
    """Caso En revisión con asignación vigente y diagnóstico vacío (el estado de partida del técnico)."""
    caso = caso_en(session, datos, "En revisión")
    asignar(session, caso, tecnico)
    session.add(Diagnostico(id_caso=caso.id_caso))
    session.flush()
    return caso


def eventos_de(session: Session, id_caso: str) -> list[tuple[str, str | None, str | None]]:
    filas = session.execute(
        text("SELECT tipo_evento, actor_tipo, actor_id FROM eventos WHERE id_caso = :c ORDER BY id_evento"),
        {"c": id_caso},
    )
    return [(f[0], f[1], f[2]) for f in filas]


def estado_de(session: Session, id_caso: str) -> str:
    return str(
        session.scalar(
            text("SELECT e.nombre_estado FROM casos c JOIN estados e USING (id_estado) WHERE c.id_caso = :c"),
            {"c": id_caso},
        )
    )


def crear_parametro(
    session: Session,
    categoria: CategoriaFitosanitaria,
    *,
    minimo: int = 3,
    km: str = "3.50",
    dias: int = 30,
) -> ParametroEpidemiologico:
    parametro = ParametroEpidemiologico(
        id_categoria=categoria.id_categoria,
        numero_minimo_casos=minimo,
        distancia_km=Decimal(km),
        intervalo_dias=dias,
        fecha_actualizacion=ahora(),
    )
    session.add(parametro)
    session.flush()
    return parametro


def caso_con_diagnostico(
    session: Session,
    datos: Escenario,
    categoria: CategoriaFitosanitaria | None,
    *,
    lat: str = "5.2800",
    lon: str = "-73.1700",
    resultado: str | None = "CONFIRMADO",
    fecha: datetime | None = None,
    estado: str = "Registrado",
) -> Caso:
    """Caso con finca en (lat, lon) y diagnóstico del técnico asignado: lo que mira la evaluación de alertas."""
    n = next(_secuencia_focos)
    productor = type(datos.productor)(
        nombre_productor=f"Productor foco {n}",
        telefono_productor=f"5734{n:08d}",
        estado_cuenta_productor="ACTIVO",
        fecha_registro_productor=date(2026, 10, 1),
    )
    finca = Finca(
        nombre_finca=f"Finca foco {n}",
        vereda_sector_finca="Centro",
        latitud_finca=Decimal(lat),
        longitud_finca=Decimal(lon),
        productor=productor,
    )
    cultivo = Cultivo(
        nombre_cultivo=f"Lote foco {n}",
        latitud_cultivo=Decimal(lat),
        longitud_cultivo=Decimal(lon),
        finca=finca,
        tipo_cultivo="INVERNADERO",
    )
    cultivo.especies_asociadas.append(CultivoEspecie(id_especie=datos.especie.id_especie))
    session.add_all([productor, finca, cultivo])
    session.flush()
    caso = Caso(
        id_cultivo=cultivo.id_cultivo,
        id_especie=datos.especie.id_especie,
        id_estado=id_estado(session, estado),
        id_identificacion_preliminar=datos.identificacion.id_identificacion,
        fecha_creacion_caso=ahora(),
    )
    session.add(caso)
    session.flush()
    tecnico = crear_tecnico(session, 1000 + n)
    asignar(session, caso, tecnico)
    session.add(
        Diagnostico(
            id_caso=caso.id_caso,
            resultado_tecnico=resultado,
            id_categoria_confirmada=categoria.id_categoria if categoria is not None else None,
            fecha_diagnostico=(fecha or ahora()) if resultado is not None else None,
        )
    )
    session.flush()
    return caso


def distancia_km(session: Session, a: Caso, b: Caso) -> float:
    return float(
        session.scalar(
            text(
                "SELECT fn_distancia_km(x.ubicacion_finca, y.ubicacion_finca) "
                "FROM vw_casos_detalle x, vw_casos_detalle y WHERE x.id_caso = :a AND y.id_caso = :b"
            ),
            {"a": a.id_caso, "b": b.id_caso},
        )
    )


ESTADOS_DEL_CASO = (
    "Caso creado",
    "En análisis",
    "Pendiente de revisión",
    "En revisión",
    "Registrado",
    "Cerrado",
    "Descartado",
)


def casos_en_todos_los_estados(session: Session, datos: Escenario) -> dict[str, Caso]:
    return {estado: caso_en(session, datos, estado) for estado in ESTADOS_DEL_CASO}
