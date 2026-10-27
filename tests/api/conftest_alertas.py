"""Escenario común de las pruebas de alertas (se importa desde los archivos de prueba, no es un conftest)."""

from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from tests.api.conftest import Mundo
from tests.api.territorio import (
    crear_alerta,
    crear_caso,
    crear_cultivo,
    crear_finca,
    crear_productor,
    registrar_diagnostico,
)


@dataclass
class Zona:
    """
    Una alerta GENERADA de Trips con tres casos confirmados (dos en La Esperanza y uno en La Cercana) y los
    productores alrededor, medidos desde las fincas de esos casos:

    - Ana (Centro, 0 km) · Álvaro (San Luis, 0 km) · Cerca (Centro, ~2,2 km) → dentro de 3,5 km y activos.
    - Deshabilitado (Centro, ~1,1 km) → dentro del radio pero no recibe mensajes.
    - Lejos (San Luis, ~6,7 km) → fuera de 3,5 km; entra si el radio es de 7 km.
    """

    alerta: str
    casos: list[str]
    ana: dict[str, Any]
    alvaro: dict[str, Any]
    cerca: dict[str, Any]
    deshabilitado: dict[str, Any]
    lejos: dict[str, Any]

    @property
    def activos_en_radio(self) -> list[dict[str, Any]]:
        return [self.ana, self.alvaro, self.cerca]


def construir_zona(api: TestClient, mundo: Mundo, motor: Engine, estado: str = "GENERADA") -> Zona:
    lider = mundo.lider
    cerca = crear_productor(api, lider, 3, nombre="Carlos Cerca")
    lejos = crear_productor(api, lider, 4, nombre="Luis Lejos")
    deshabilitado = crear_productor(api, lider, 5, nombre="Diego Deshabilitado")
    f_cerca = crear_finca(api, lider, cerca["id"], "La Cercana", "Centro", 5.27, -73.17)
    f_lejos = crear_finca(api, lider, lejos["id"], "La Lejana", "San Luis", 5.33, -73.17)
    f_deshab = crear_finca(api, lider, deshabilitado["id"], "La Dormida", "Centro", 5.26, -73.17)
    cultivo_cerca = crear_cultivo(api, lider, f_cerca["id"])["cultivos"][0]["id"]
    crear_cultivo(api, lider, f_lejos["id"])
    crear_cultivo(api, lider, f_deshab["id"])
    api.patch(f"/api/v1/productores/{deshabilitado['id']}/estado", json={"activo": False}, headers=lider.headers)

    casos = [
        crear_caso(motor, mundo.cultivo1, "GULUPA", "Registrado", tecnico=mundo.t1.id),
        crear_caso(motor, mundo.cultivo1, "GULUPA", "Registrado", tecnico=mundo.t1.id),
        crear_caso(motor, cultivo_cerca, "GULUPA", "Registrado", tecnico=mundo.t2.id),
    ]
    for id_caso in casos:
        registrar_diagnostico(motor, id_caso, "CONFIRMADO", "TRIPS")
    return Zona(
        alerta=crear_alerta(motor, casos, estado),
        casos=casos,
        ana=mundo.productor1,
        alvaro=mundo.productor2,
        cerca=cerca,
        deshabilitado=deshabilitado,
        lejos=lejos,
    )
