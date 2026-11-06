"""PATCH /me (Mi cuenta) y POST /me/contrasena."""

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.core.roles import Rol
from app.infrastructure.cognito_admin_client import CognitoSimulado, get_cognito_admin
from app.main import app
from tests.api.conftest import CognitoQueFalla, NuevaCuenta, auditoria_de, consultar

ME = "/api/v1/me"


# --- PATCH /me ---------------------------------------------------------------------------------------------------


def test_un_tecnico_edita_su_nombre(api: TestClient, nueva_cuenta: NuevaCuenta, migrated: Engine, cognito) -> None:  # type: ignore[no-untyped-def]
    ana = nueva_cuenta(Rol.TECNICO)
    r = api.patch(ME, json={"nombre": "Ana María Torres"}, headers=ana.headers)
    assert r.status_code == 200
    assert r.json() == {
        "id": ana.id,
        "nombre": "Ana María Torres",
        "correo": ana.correo,
        "celular": ana.celular,
        "rol": "TECNICO",
        "activo": True,
    }
    assert consultar(migrated, "SELECT nombre_tecnico FROM tecnicos")[0]["nombre_tecnico"] == "Ana María Torres"
    assert cognito.obtener(ana.correo).nombre == "Ana María Torres"  # también en Cognito


def test_un_lider_edita_su_celular(api: TestClient, nueva_cuenta: NuevaCuenta, migrated: Engine, cognito) -> None:  # type: ignore[no-untyped-def]
    luis = nueva_cuenta(Rol.LIDER_TECNICO)
    r = api.patch(ME, json={"celular": "3109998877"}, headers=luis.headers)
    assert r.status_code == 200 and r.json()["celular"] == "3109998877" and r.json()["rol"] == "LIDER_TECNICO"
    assert (
        consultar(migrated, "SELECT celular_lider_tecnico FROM lideres_tecnicos")[0]["celular_lider_tecnico"]
        == "573109998877"
    )
    assert cognito.obtener(luis.correo).celular == "573109998877"


def test_edita_nombre_y_celular_a_la_vez(api: TestClient, nueva_cuenta: NuevaCuenta) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    r = api.patch(ME, json={"nombre": "Ana T.", "celular": "3201112233"}, headers=ana.headers)
    assert (r.json()["nombre"], r.json()["celular"]) == ("Ana T.", "3201112233")


def test_el_get_me_refleja_el_cambio(api: TestClient, nueva_cuenta: NuevaCuenta) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    api.patch(ME, json={"nombre": "Nuevo Nombre"}, headers=ana.headers)
    assert api.get(ME, headers=ana.headers).json()["nombre"] == "Nuevo Nombre"


def test_cambia_el_correo_en_la_base_y_en_cognito(
    api: TestClient, nueva_cuenta: NuevaCuenta, migrated: Engine, cognito: CognitoSimulado
) -> None:
    """B-04: el panel envía el correo; si cambió, se actualiza en las dos partes y se audita."""
    ana = nueva_cuenta(Rol.TECNICO)
    r = api.patch(ME, json={"correo": " Ana.Nueva@ANUC.org "}, headers=ana.headers)
    assert r.status_code == 200 and r.json()["correo"] == "ana.nueva@anuc.org"
    assert consultar(migrated, "SELECT correo_tecnico FROM tecnicos")[0]["correo_tecnico"] == "ana.nueva@anuc.org"
    assert cognito.existe("ana.nueva@anuc.org") and not cognito.existe(ana.correo)
    assert api.get(ME, headers=ana.headers).status_code == 200  # la sesión sigue (se reconoce por sub)


def test_el_panel_envia_los_tres_campos_y_el_mismo_correo_no_es_cambio(
    api: TestClient, nueva_cuenta: NuevaCuenta, migrated: Engine
) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    cuerpo = {"nombre": "Ana Nueva", "correo": ana.correo, "celular": "+57" + ana.celular}
    r = api.patch(ME, json=cuerpo, headers=ana.headers)
    assert r.status_code == 200 and r.json()["nombre"] == "Ana Nueva" and r.json()["correo"] == ana.correo


def test_el_correo_de_otra_cuenta_es_409(api: TestClient, nueva_cuenta: NuevaCuenta, migrated: Engine) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    luis = nueva_cuenta(Rol.TECNICO, 2)
    r = api.patch(ME, json={"correo": luis.correo}, headers=ana.headers)
    assert r.status_code == 409 and r.json()["campos"] == {"correo": "Este correo ya está registrado"}
    assert (
        consultar(migrated, "SELECT correo_tecnico FROM tecnicos ORDER BY id_tecnico")[0]["correo_tecnico"]
        == ana.correo
    )


def test_correo_invalido_422(api: TestClient, nueva_cuenta: NuevaCuenta) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    r = api.patch(ME, json={"correo": "malo"}, headers=ana.headers)
    assert r.status_code == 422 and "correo" in r.json()["campos"]


def test_sin_cambios_no_toca_nada_ni_audita(api: TestClient, nueva_cuenta: NuevaCuenta, migrated: Engine) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    for cuerpo in (
        {},
        {"nombre": ana.nombre},
        {"celular": ana.celular},
        {"nombre": ana.nombre, "celular": ana.celular},
    ):
        assert api.patch(ME, json=cuerpo, headers=ana.headers).status_code == 200
    assert auditoria_de(migrated) == []


@pytest.mark.parametrize(
    ("cuerpo", "campo"),
    [
        ({"nombre": "  "}, "nombre"),
        ({"nombre": "x" * 101}, "nombre"),
        ({"celular": "2001234567"}, "celular"),
        ({"celular": "abc"}, "celular"),
    ],
)
def test_datos_invalidos_422(api: TestClient, nueva_cuenta: NuevaCuenta, cuerpo: dict[str, str], campo: str) -> None:
    r = api.patch(ME, json=cuerpo, headers=nueva_cuenta(Rol.TECNICO).headers)
    assert r.status_code == 422 and campo in r.json()["campos"]


def test_celular_de_otro_tecnico_409(api: TestClient, nueva_cuenta: NuevaCuenta, migrated: Engine) -> None:
    ana, beto = nueva_cuenta(Rol.TECNICO, 1), nueva_cuenta(Rol.TECNICO, 2)
    r = api.patch(ME, json={"celular": beto.celular}, headers=ana.headers)
    assert r.status_code == 409 and r.json()["codigo"] == "DUPLICADO"
    assert r.json()["campos"] == {"celular": "Este número de celular ya está registrado"}
    assert auditoria_de(migrated) == []


def test_el_lider_y_su_cuenta_de_tecnico_pueden_compartir_celular(api: TestClient, nueva_cuenta: NuevaCuenta) -> None:
    """Son la misma persona con dos cuentas (D-02); el celular es único dentro de cada tabla."""
    luis = nueva_cuenta(Rol.LIDER_TECNICO)
    luis_tecnico = nueva_cuenta(Rol.TECNICO, correo="luis.tecnico@anuc.org", celular="3200000001")
    assert api.patch(ME, json={"celular": luis.celular}, headers=luis_tecnico.headers).status_code == 200


def test_audita_con_el_rol_de_quien_edita_y_sin_el_celular_completo(
    api: TestClient, nueva_cuenta: NuevaCuenta, migrated: Engine
) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    api.patch(ME, json={"nombre": "Ana Nueva", "celular": "3201112233"}, headers=ana.headers)
    [registro] = auditoria_de(migrated)
    assert (registro["origen"], registro["id_tecnico"], registro["id_lider_tecnico"]) == ("TECNICO", ana.id, None)
    assert (registro["accion"], registro["entidad_afectada"]) == ("EDITAR_MI_CUENTA", "tecnicos")
    texto = " ".join(str(v) for v in registro.values())
    assert "3201112233" not in texto and "573201112233" not in texto  # el celular no queda en la auditoría
    assert "Ana Nueva" in texto


def test_un_lider_audita_como_lider(api: TestClient, nueva_cuenta: NuevaCuenta, migrated: Engine) -> None:
    luis = nueva_cuenta(Rol.LIDER_TECNICO)
    api.patch(ME, json={"nombre": "Luis P."}, headers=luis.headers)
    [registro] = auditoria_de(migrated)
    assert (registro["origen"], registro["id_lider_tecnico"], registro["entidad_afectada"]) == (
        "LIDER",
        luis.id,
        "lideres_tecnicos",
    )


def test_sin_sesion_401(api: TestClient) -> None:
    assert api.patch(ME, json={"nombre": "X"}).status_code == 401


def test_una_cuenta_deshabilitada_no_puede_editar(api: TestClient, nueva_cuenta: NuevaCuenta) -> None:
    ana = nueva_cuenta(Rol.TECNICO, activo=False)
    r = api.patch(ME, json={"nombre": "X"}, headers=ana.headers)
    assert r.status_code == 403 and r.json()["codigo"] == "CUENTA_INACTIVA"


def test_si_cognito_falla_la_base_no_cambia(api: TestClient, nueva_cuenta: NuevaCuenta, migrated: Engine) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    app.dependency_overrides[get_cognito_admin] = lambda: CognitoQueFalla("actualizar")
    r = api.patch(ME, json={"nombre": "Otro Nombre"}, headers=ana.headers)
    assert r.status_code == 503 and r.json()["codigo"] == "ERROR_INTERNO"
    assert consultar(migrated, "SELECT nombre_tecnico FROM tecnicos")[0]["nombre_tecnico"] == ana.nombre
    assert auditoria_de(migrated) == []


# --- POST /me/contrasena -----------------------------------------------------------------------------------------


def clave(api: TestClient, headers: dict[str, str], actual: str, nueva: str) -> object:
    return api.post(f"{ME}/contrasena", json={"actual": actual, "nueva": nueva}, headers=headers)


@pytest.fixture
def con_clave(nueva_cuenta: NuevaCuenta, cognito) -> object:  # type: ignore[no-untyped-def]
    ana = nueva_cuenta(Rol.TECNICO)
    cognito.obtener(ana.correo).contrasena = "Clave1234"
    return ana


def test_cambia_la_contrasena(api: TestClient, con_clave, cognito) -> None:  # type: ignore[no-untyped-def]
    r = clave(api, con_clave.headers, "Clave1234", "Nueva5678")
    assert r.status_code == 204 and r.content == b""
    assert cognito.obtener(con_clave.correo).contrasena == "Nueva5678"


def test_la_contrasena_actual_incorrecta_es_422_en_el_campo_actual(api: TestClient, con_clave, cognito) -> None:  # type: ignore[no-untyped-def]
    r = clave(api, con_clave.headers, "equivocada1", "Nueva5678")
    assert r.status_code == 422 and r.json()["campos"] == {"actual": "La contraseña actual no es correcta"}
    assert cognito.obtener(con_clave.correo).contrasena == "Clave1234"


@pytest.mark.parametrize("nueva", ["corta1", "sinnumeros", "12345678", ""])
def test_la_nueva_debe_cumplir_la_politica(api: TestClient, con_clave, nueva: str) -> None:  # type: ignore[no-untyped-def]
    r = clave(api, con_clave.headers, "Clave1234", nueva)
    assert r.status_code == 422 and "nueva" in r.json()["campos"]


def test_si_cognito_rechaza_la_nueva_el_error_va_en_nueva(
    api: TestClient, con_clave, cognito, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    from app.infrastructure.cognito_admin_client import ContrasenaNoValida

    def rechaza(*_a: object, **_k: object) -> None:
        raise ContrasenaNoValida

    monkeypatch.setattr(cognito, "cambiar_contrasena", rechaza)
    r = clave(api, con_clave.headers, "Clave1234", "Nueva5678")
    assert r.status_code == 422 and "nueva" in r.json()["campos"]


def test_sin_sesion_no_se_puede_cambiar(api: TestClient) -> None:
    assert api.post(f"{ME}/contrasena", json={"actual": "a", "nueva": "Nueva5678"}).status_code == 401


def test_requiere_la_actual(api: TestClient, con_clave) -> None:  # type: ignore[no-untyped-def]
    r = api.post(f"{ME}/contrasena", json={"nueva": "Nueva5678"}, headers=con_clave.headers)
    assert r.status_code == 422 and "actual" in r.json()["campos"]


def test_audita_el_cambio_sin_ninguna_contrasena(
    api: TestClient,
    con_clave,
    migrated: Engine,
    caplog: pytest.LogCaptureFixture,  # type: ignore[no-untyped-def]
) -> None:
    with caplog.at_level(logging.DEBUG):
        clave(api, con_clave.headers, "Clave1234", "Nueva5678")
    [registro] = auditoria_de(migrated)
    assert (registro["origen"], registro["id_tecnico"], registro["accion"]) == (
        "TECNICO",
        con_clave.id,
        "CAMBIAR_CLAVE",
    )
    todo = " ".join(str(v) for v in registro.values()) + caplog.text
    assert "Clave1234" not in todo and "Nueva5678" not in todo


def test_un_cambio_fallido_no_se_audita(api: TestClient, con_clave, migrated: Engine) -> None:  # type: ignore[no-untyped-def]
    clave(api, con_clave.headers, "equivocada1", "Nueva5678")
    assert auditoria_de(migrated) == []


def test_una_cuenta_deshabilitada_no_cambia_la_contrasena(api: TestClient, nueva_cuenta: NuevaCuenta) -> None:
    ana = nueva_cuenta(Rol.TECNICO, activo=False)
    assert clave(api, ana.headers, "Clave1234", "Nueva5678").status_code == 403


def test_si_cognito_falla_es_503(api: TestClient, con_clave) -> None:  # type: ignore[no-untyped-def]
    app.dependency_overrides[get_cognito_admin] = lambda: CognitoQueFalla("cambiar_contrasena")
    r = clave(api, con_clave.headers, "Clave1234", "Nueva5678")
    assert r.status_code == 503 and r.json()["codigo"] == "ERROR_INTERNO"
