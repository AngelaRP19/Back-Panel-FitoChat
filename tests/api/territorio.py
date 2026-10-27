"""Ayudas de las pruebas del módulo 3: crear productores, fincas, cultivos y casos reales con datos confirmados."""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core.catalogos_api import ESPECIES
from app.infrastructure.db import procedimientos as sp
from app.infrastructure.db.models import Caso, Tecnico
from tests.api.conftest import Cuenta
from tests.infrastructure.db.factories import asignar

PRODUCTORES = "/api/v1/productores"
FINCAS = "/api/v1/fincas"


def crear_productor(api: TestClient, lider: Cuenta, n: int = 1, **extra: Any) -> dict[str, Any]:
    cuerpo = {"nombre": f"Productor {n}", "celular": f"3001112{n:03d}", **extra}
    r = api.post(PRODUCTORES, json=cuerpo, headers=lider.headers)
    assert r.status_code == 201, r.text
    return dict(r.json())


def crear_finca(
    api: TestClient,
    lider: Cuenta,
    id_productor: str,
    nombre: str = "La Esperanza",
    vereda: str = "Centro",
    latitud: float = 5.25,
    longitud: float = -73.17,
) -> dict[str, Any]:
    cuerpo = {
        "nombre": nombre,
        "vereda": vereda,
        "latitud": latitud,
        "longitud": longitud,
        "productor_id": id_productor,
    }
    r = api.post(FINCAS, json=cuerpo, headers=lider.headers)
    assert r.status_code == 201, r.text
    return dict(r.json())


def crear_cultivo(
    api: TestClient,
    lider: Cuenta,
    id_finca: str,
    nombre: str = "Lote 1",
    tipo: str = "CAMPO_ABIERTO",
    especies: tuple[str, ...] = ("GULUPA",),
) -> dict[str, Any]:
    """Devuelve la finca actualizada (así responde `POST /fincas/{id}/cultivos`)."""
    r = api.post(
        f"{FINCAS}/{id_finca}/cultivos",
        json={"nombre": nombre, "tipo": tipo, "especies": list(especies)},
        headers=lider.headers,
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


def crear_caso(
    motor: Engine,
    id_cultivo: str,
    especie: str = "GULUPA",
    estado: str = "En revisión",
    *,
    tecnico: str | None = None,
    vigente: bool = True,
    dias_atras: int = 0,
) -> str:
    """Caso real del cultivo y la especie (el par debe existir); con `tecnico`, asignado a esa cuenta."""
    with Session(motor) as sesion:
        id_especie = sesion.scalar(
            text("SELECT id_especie FROM especies WHERE nombre_especie = :n"), {"n": ESPECIES.a_nombre(especie)}
        )
        caso = Caso(
            id_cultivo=id_cultivo,
            id_especie=id_especie,
            id_estado=str(sesion.scalar(sp.ID_ESTADO, {"nombre": estado})),
            fecha_creacion_caso=datetime.now(UTC) - timedelta(days=dias_atras),
        )
        sesion.add(caso)
        sesion.flush()
        if tecnico is not None:
            asignar(sesion, caso, sesion.get(Tecnico, tecnico), vigente=vigente)  # type: ignore[arg-type]
        sesion.commit()
        return caso.id_caso
