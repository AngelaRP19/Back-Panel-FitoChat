"""`requiere_rol` con rutas mínimas de prueba: la matriz de permisos que usarán los módulos siguientes."""

from collections.abc import Iterator
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.core.roles import Rol
from app.core.security_cognito import UsuarioActual, requiere_rol
from app.infrastructure.db.session import get_session
from app.main import create_app
from tests.conftest import CrearCuenta, _sesion_por_solicitud, con_token

SUB_TECNICO = "aaaaaaaa-0000-0000-0000-000000000011"
SUB_LIDER = "bbbbbbbb-0000-0000-0000-000000000012"


@pytest.fixture
def cliente(migrated: Engine) -> Iterator[TestClient]:
    aplicacion: FastAPI = create_app()
    router = APIRouter()

    @router.get("/solo-lider")
    async def solo_lider(u: Annotated[UsuarioActual, Depends(requiere_rol(Rol.LIDER_TECNICO))]) -> dict[str, str]:
        return {"id": u.id}

    @router.get("/ambos")
    async def ambos(
        u: Annotated[UsuarioActual, Depends(requiere_rol(Rol.LIDER_TECNICO, Rol.TECNICO))],
    ) -> dict[str, str]:
        return {"id": u.id}

    aplicacion.include_router(router)
    aplicacion.dependency_overrides[get_session] = _sesion_por_solicitud
    with TestClient(aplicacion, raise_server_exceptions=False) as c:
        yield c


def test_tecnico_no_entra_a_ruta_de_lider(cliente: TestClient, crear_cuenta: CrearCuenta) -> None:
    crear_cuenta(Rol.TECNICO, sub=SUB_TECNICO, numero=4)
    r = cliente.get("/solo-lider", headers=con_token(SUB_TECNICO))
    assert r.status_code == 403
    assert r.json()["codigo"] == "SIN_PERMISO"


def test_lider_entra_a_ruta_de_lider(cliente: TestClient, crear_cuenta: CrearCuenta) -> None:
    crear_cuenta(Rol.LIDER_TECNICO, sub=SUB_LIDER, numero=5)
    assert cliente.get("/solo-lider", headers=con_token(SUB_LIDER)).status_code == 200


@pytest.mark.parametrize(("rol", "sub", "numero"), [(Rol.TECNICO, SUB_TECNICO, 6), (Rol.LIDER_TECNICO, SUB_LIDER, 7)])
def test_ruta_para_ambos_roles(cliente: TestClient, crear_cuenta: CrearCuenta, rol: Rol, sub: str, numero: int) -> None:
    crear_cuenta(rol, sub=sub, numero=numero)
    assert cliente.get("/ambos", headers=con_token(sub)).status_code == 200


def test_sin_token_es_401_antes_que_403(cliente: TestClient) -> None:
    assert cliente.get("/solo-lider").status_code == 401
