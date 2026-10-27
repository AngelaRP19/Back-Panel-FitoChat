"""GET /api/v1/me contra Postgres real (los tests se omiten si no hay base)."""

import time

import pytest
from fastapi.testclient import TestClient

from app.core.roles import Rol
from tests.conftest import CrearCuenta, con_token

URL = "/api/v1/me"
SUB_TECNICO = "aaaaaaaa-0000-0000-0000-000000000001"
SUB_LIDER = "bbbbbbbb-0000-0000-0000-000000000002"


def _esperar_formato_de_error(respuesta_json: dict[str, object]) -> None:
    """Formato único {codigo, mensaje, campos?}: plano, sin envoltorio ni detalles técnicos."""
    assert set(respuesta_json) <= {"codigo", "mensaje", "campos"}
    assert isinstance(respuesta_json["codigo"], str) and isinstance(respuesta_json["mensaje"], str)


def test_sin_token_401(cliente_panel: TestClient) -> None:
    r = cliente_panel.get(URL)
    assert r.status_code == 401
    assert r.json()["codigo"] == "NO_AUTENTICADO"
    assert r.headers["www-authenticate"] == "Bearer"
    _esperar_formato_de_error(r.json())


def test_esquema_distinto_de_bearer_401(cliente_panel: TestClient) -> None:
    assert cliente_panel.get(URL, headers={"Authorization": "Basic dXNlcjpwYXNz"}).status_code == 401


def test_token_basura_401(cliente_panel: TestClient) -> None:
    r = cliente_panel.get(URL, headers={"Authorization": "Bearer esto-no-es-un-jwt"})
    assert r.status_code == 401 and r.json()["codigo"] == "NO_AUTENTICADO"


def test_token_vencido_401_token_vencido(cliente_panel: TestClient) -> None:
    r = cliente_panel.get(URL, headers=con_token(SUB_TECNICO, ahora=time.time() - 7200, expira_en=3600))
    assert r.status_code == 401
    assert r.json()["codigo"] == "TOKEN_VENCIDO"


def test_client_id_incorrecto_401(cliente_panel: TestClient) -> None:
    assert cliente_panel.get(URL, headers=con_token(SUB_TECNICO, client_id="otro")).status_code == 401


def test_id_token_401(cliente_panel: TestClient) -> None:
    assert cliente_panel.get(URL, headers=con_token(SUB_TECNICO, token_use="id")).status_code == 401


def test_tecnico_devuelve_su_rol(cliente_panel: TestClient, crear_cuenta: CrearCuenta) -> None:
    id_tecnico = crear_cuenta(Rol.TECNICO, sub=SUB_TECNICO, numero=1)
    r = cliente_panel.get(URL, headers=con_token(SUB_TECNICO))
    assert r.status_code == 200
    assert r.json() == {
        "id": id_tecnico,
        "nombre": "Ana Técnica",
        "correo": "ana1@demo.test",
        "celular": "3000000001",  # 10 dígitos en la API aunque se guarde en E.164
        "rol": "TECNICO",
        "activo": True,
    }


def test_lider_devuelve_su_rol(cliente_panel: TestClient, crear_cuenta: CrearCuenta) -> None:
    id_lider = crear_cuenta(Rol.LIDER_TECNICO, sub=SUB_LIDER, numero=2)
    r = cliente_panel.get(URL, headers=con_token(SUB_LIDER))
    assert r.status_code == 200
    assert r.json()["id"] == id_lider and id_lider.startswith("LID-")
    assert r.json()["rol"] == "LIDER_TECNICO"


def test_cada_token_resuelve_su_cuenta(cliente_panel: TestClient, crear_cuenta: CrearCuenta) -> None:
    crear_cuenta(Rol.TECNICO, sub=SUB_TECNICO, numero=1)
    crear_cuenta(Rol.LIDER_TECNICO, sub=SUB_LIDER, numero=2)
    assert cliente_panel.get(URL, headers=con_token(SUB_TECNICO)).json()["rol"] == "TECNICO"
    assert cliente_panel.get(URL, headers=con_token(SUB_LIDER)).json()["rol"] == "LIDER_TECNICO"


@pytest.mark.parametrize("rol", [Rol.TECNICO, Rol.LIDER_TECNICO])
def test_cuenta_inactiva_403(cliente_panel: TestClient, crear_cuenta: CrearCuenta, rol: Rol) -> None:
    crear_cuenta(rol, sub=SUB_TECNICO, activo=False, numero=3)
    r = cliente_panel.get(URL, headers=con_token(SUB_TECNICO))
    assert r.status_code == 403
    assert r.json()["codigo"] == "CUENTA_INACTIVA"
    _esperar_formato_de_error(r.json())


def test_sub_sin_cuenta_403_sin_permiso(cliente_panel: TestClient) -> None:
    r = cliente_panel.get(URL, headers=con_token("cccccccc-0000-0000-0000-000000000009"))
    assert r.status_code == 403
    assert r.json()["codigo"] == "SIN_PERMISO"


def test_la_respuesta_no_expone_datos_internos(cliente_panel: TestClient, crear_cuenta: CrearCuenta) -> None:
    crear_cuenta(Rol.TECNICO, sub=SUB_TECNICO, numero=1)
    cuerpo = cliente_panel.get(URL, headers=con_token(SUB_TECNICO)).text
    for oculto in ("cognito_sub", SUB_TECNICO, "hash-inutilizable", "contrasena", "573000000001"):
        assert oculto not in cuerpo


def test_openapi_documenta_me_y_sus_errores(cliente_panel: TestClient) -> None:
    spec = cliente_panel.get("/openapi.json").json()
    operacion = spec["paths"]["/api/v1/me"]["get"]
    assert set(operacion["responses"]) >= {"200", "401", "403"}
    assert "UsuarioMe" in spec["components"]["schemas"]
    assert set(spec["components"]["schemas"]["UsuarioMe"]["properties"]) == {
        "id", "nombre", "correo", "celular", "rol", "activo"
    }  # fmt: skip
