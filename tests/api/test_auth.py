"""GET /auth/estado, POST /auth/primer-lider y POST /auth/recuperacion/verificar-correo."""

import asyncio
import logging

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.core.roles import Rol
from app.infrastructure.cognito_admin_client import get_cognito_admin
from app.main import app
from app.modules.panel_api.cuentas import service as servicio
from app.modules.panel_api.cuentas.router_auth import LIMITADOR_VERIFICAR_CORREO
from tests.api.conftest import CognitoQueFalla, NuevaCuenta, auditoria_de, consultar

PRIMER_LIDER = {"nombre": "Luis Pérez", "correo": "luis@anuc.org", "celular": "3001234567", "password": "Clave1234"}
URL = "/api/v1/auth"


# --- GET /auth/estado --------------------------------------------------------------------------------------------


def test_sin_lider_el_panel_pide_el_primer_uso(api: TestClient) -> None:
    r = api.get(f"{URL}/estado")
    assert r.status_code == 200 and r.json() == {"hay_lider": False}


def test_con_lider_ya_no_hay_primer_uso(api: TestClient, nueva_cuenta: NuevaCuenta) -> None:
    nueva_cuenta(Rol.LIDER_TECNICO)
    assert api.get(f"{URL}/estado").json() == {"hay_lider": True}


def test_un_lider_deshabilitado_sigue_contando(api: TestClient, nueva_cuenta: NuevaCuenta) -> None:
    """Si no, cualquiera podría crear un líder nuevo en cuanto se deshabilitara el único."""
    nueva_cuenta(Rol.LIDER_TECNICO, activo=False)
    assert api.get(f"{URL}/estado").json() == {"hay_lider": True}


def test_un_tecnico_no_cuenta_como_lider(api: TestClient, nueva_cuenta: NuevaCuenta) -> None:
    nueva_cuenta(Rol.TECNICO)
    assert api.get(f"{URL}/estado").json() == {"hay_lider": False}


def test_el_estado_es_publico(api: TestClient) -> None:
    assert api.get(f"{URL}/estado", headers={"Authorization": "Bearer basura"}).status_code == 200


# --- POST /auth/primer-lider -------------------------------------------------------------------------------------


def test_crea_al_primer_lider(api: TestClient, migrated: Engine) -> None:
    r = api.post(f"{URL}/primer-lider", json=PRIMER_LIDER)
    assert r.status_code == 201
    id_lider = r.json()["id"]
    assert set(r.json()) == {"id"} and id_lider.startswith("LID-")
    fila = consultar(migrated, "SELECT * FROM lideres_tecnicos")[0]
    assert fila["id_lider_tecnico"] == id_lider and fila["estado_cuenta_lider_tecnico"] == "ACTIVO"
    assert fila["nombre_lider_tecnico"] == "Luis Pérez" and fila["celular_lider_tecnico"] == "573001234567"  # E.164
    assert api.get(f"{URL}/estado").json() == {"hay_lider": True}


def test_la_cuenta_queda_enlazada_a_cognito_con_su_contrasena(api: TestClient, migrated: Engine, cognito) -> None:  # type: ignore[no-untyped-def]
    api.post(f"{URL}/primer-lider", json=PRIMER_LIDER)
    fila = consultar(migrated, "SELECT * FROM lideres_tecnicos")[0]
    usuario = cognito.obtener("luis@anuc.org")
    assert fila["cognito_sub"] == usuario.sub
    assert usuario.contrasena == "Clave1234" and usuario.celular == "573001234567"  # el líder define su contraseña


def test_la_contrasena_nunca_se_guarda_ni_se_devuelve(
    api: TestClient, migrated: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        r = api.post(f"{URL}/primer-lider", json=PRIMER_LIDER)
    assert "Clave1234" not in r.text and "Clave1234" not in caplog.text
    fila = consultar(migrated, "SELECT * FROM lideres_tecnicos")[0]
    assert "Clave1234" not in " ".join(str(v) for v in fila.values())  # ni siquiera su hash legible
    assert fila["contrasena_lider_tecnico"].startswith("$argon2id$")  # hash de un valor aleatorio, no de la clave
    assert "Clave1234" not in " ".join(str(v) for f in auditoria_de(migrated) for v in f.values())


def test_audita_el_alta_con_el_nuevo_lider_como_autor(api: TestClient, migrated: Engine) -> None:
    id_lider = api.post(f"{URL}/primer-lider", json=PRIMER_LIDER).json()["id"]
    [registro] = auditoria_de(migrated)
    assert (registro["origen"], registro["id_lider_tecnico"], registro["accion"]) == (
        "LIDER",
        id_lider,
        "ALTA_PRIMER_LIDER",
    )
    assert registro["entidad_afectada"] == "lideres_tecnicos" and registro["resultado"] == "EXITO"


def test_normaliza_el_correo_y_el_nombre(api: TestClient, migrated: Engine, cognito) -> None:  # type: ignore[no-untyped-def]
    api.post(f"{URL}/primer-lider", json={**PRIMER_LIDER, "correo": " LUIS@Anuc.ORG ", "nombre": "  Luis Pérez  "})
    fila = consultar(migrated, "SELECT * FROM lideres_tecnicos")[0]
    assert (fila["correo_lider_tecnico"], fila["nombre_lider_tecnico"]) == ("luis@anuc.org", "Luis Pérez")
    assert cognito.existe("luis@anuc.org")


def test_el_segundo_intento_es_409_y_no_toca_cognito(api: TestClient, migrated: Engine, cognito) -> None:  # type: ignore[no-untyped-def]
    api.post(f"{URL}/primer-lider", json=PRIMER_LIDER)
    r = api.post(f"{URL}/primer-lider", json={**PRIMER_LIDER, "correo": "otro@anuc.org", "celular": "3109998877"})
    assert r.status_code == 409 and r.json()["codigo"] == "YA_EXISTE_LIDER"
    assert not cognito.existe("otro@anuc.org")
    assert len(consultar(migrated, "SELECT 1 FROM lideres_tecnicos")) == 1


def test_si_ya_hay_un_lider_deshabilitado_tambien_es_409(api: TestClient, nueva_cuenta: NuevaCuenta) -> None:
    nueva_cuenta(Rol.LIDER_TECNICO, activo=False)
    assert api.post(f"{URL}/primer-lider", json=PRIMER_LIDER).json()["codigo"] == "YA_EXISTE_LIDER"


def test_puede_haber_tecnicos_antes_del_primer_lider(api: TestClient, nueva_cuenta: NuevaCuenta) -> None:
    nueva_cuenta(Rol.TECNICO, correo="luis.tecnico@anuc.org")
    assert api.post(f"{URL}/primer-lider", json=PRIMER_LIDER).status_code == 201


def test_el_correo_de_un_tecnico_no_se_puede_reusar(api: TestClient, nueva_cuenta: NuevaCuenta) -> None:
    nueva_cuenta(Rol.TECNICO, correo="luis@anuc.org")
    r = api.post(f"{URL}/primer-lider", json=PRIMER_LIDER)
    assert r.status_code == 409 and r.json()["codigo"] == "DUPLICADO"
    assert r.json()["campos"] == {"correo": "Este correo ya está registrado"}


@pytest.mark.parametrize(
    ("cambio", "campo"),
    [
        ({"password": "corta1"}, "password"),
        ({"password": "sinnumeros"}, "password"),
        ({"password": "12345678"}, "password"),
        ({"correo": "no-es-correo"}, "correo"),
        ({"celular": "573001234567"}, "celular"),
        ({"celular": "2001234567"}, "celular"),
        ({"nombre": "   "}, "nombre"),
    ],
)
def test_datos_invalidos_422_con_el_campo(api: TestClient, cambio: dict[str, str], campo: str, cognito) -> None:  # type: ignore[no-untyped-def]
    r = api.post(f"{URL}/primer-lider", json={**PRIMER_LIDER, **cambio})
    assert r.status_code == 422 and r.json()["codigo"] == "VALIDACION"
    assert campo in r.json()["campos"]
    assert not cognito.existe("luis@anuc.org")  # no se creó nada


def test_faltan_campos(api: TestClient) -> None:
    r = api.post(f"{URL}/primer-lider", json={})
    assert r.status_code == 422 and set(r.json()["campos"]) == {"nombre", "correo", "celular", "password"}


def test_el_mensaje_de_validacion_va_en_espanol_y_sin_prefijo(api: TestClient) -> None:
    campos = api.post(f"{URL}/primer-lider", json={**PRIMER_LIDER, "password": "corta"}).json()["campos"]
    assert campos["password"] == "Mínimo 8 caracteres, con letras y números."


def test_si_cognito_rechaza_la_contrasena_es_422(api: TestClient, cognito, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from app.infrastructure.cognito_admin_client import ContrasenaNoValida

    def rechaza(*_: object, **__: object) -> str:
        raise ContrasenaNoValida

    monkeypatch.setattr(cognito, "crear_usuario", rechaza)
    r = api.post(f"{URL}/primer-lider", json=PRIMER_LIDER)
    assert r.status_code == 422 and "password" in r.json()["campos"]


def test_si_cognito_falla_no_queda_nada_en_la_base(api: TestClient, migrated: Engine) -> None:
    app.dependency_overrides[get_cognito_admin] = lambda: CognitoQueFalla("crear")
    r = api.post(f"{URL}/primer-lider", json=PRIMER_LIDER)
    assert r.status_code == 503 and r.json()["codigo"] == "ERROR_INTERNO"
    assert "Throttling" not in r.text  # sin detalles técnicos hacia el cliente
    assert consultar(migrated, "SELECT 1 FROM lideres_tecnicos") == [] and auditoria_de(migrated) == []
    assert api.get(f"{URL}/estado").json() == {"hay_lider": False}  # se puede reintentar


def test_si_la_base_falla_se_elimina_el_usuario_de_cognito(
    api: TestClient,
    migrated: Engine,
    cognito,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    async def explota(*_: object, **__: object) -> str:
        raise RuntimeError("base caída")

    monkeypatch.setattr(servicio.repo, "crear", explota)
    r = api.post(f"{URL}/primer-lider", json=PRIMER_LIDER)
    assert r.status_code == 500 and r.json()["codigo"] == "ERROR_INTERNO"
    assert cognito.eliminados == ["luis@anuc.org"] and not cognito.existe("luis@anuc.org")
    assert consultar(migrated, "SELECT 1 FROM lideres_tecnicos") == []


def test_dos_solicitudes_a_la_vez_solo_crean_un_lider(api: TestClient, migrated: Engine, cognito) -> None:  # type: ignore[no-untyped-def]
    """El candado transaccional evita la carrera: una gana (201) y la otra recibe YA_EXISTE_LIDER."""

    async def disparar() -> list[httpx.Response]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as cliente:
            return await asyncio.gather(
                *(
                    cliente.post(
                        f"{URL}/primer-lider",
                        json={**PRIMER_LIDER, "correo": f"lider{i}@anuc.org", "celular": f"310000000{i}"},
                    )
                    for i in range(4)
                )
            )

    respuestas = asyncio.run(disparar())
    assert sorted(r.status_code for r in respuestas) == [201, 409, 409, 409]
    assert {r.json()["codigo"] for r in respuestas if r.status_code == 409} == {"YA_EXISTE_LIDER"}
    assert len(consultar(migrated, "SELECT 1 FROM lideres_tecnicos")) == 1
    assert sum(cognito.existe(f"lider{i}@anuc.org") for i in range(4)) == 1  # y un solo usuario en Cognito


# --- POST /auth/recuperacion/verificar-correo --------------------------------------------------------------------


def verificar(api: TestClient, correo: str) -> httpx.Response:
    return api.post(f"{URL}/recuperacion/verificar-correo", json={"correo": correo})


def test_correo_registrado(api: TestClient, nueva_cuenta: NuevaCuenta) -> None:
    nueva_cuenta(Rol.TECNICO, correo="ana@anuc.org")
    r = verificar(api, "ana@anuc.org")
    assert r.status_code == 200 and r.json() == {"existe": True}


def test_correo_de_un_lider_tambien_existe(api: TestClient, nueva_cuenta: NuevaCuenta) -> None:
    nueva_cuenta(Rol.LIDER_TECNICO, correo="luis@anuc.org")
    assert verificar(api, "luis@anuc.org").json() == {"existe": True}


def test_correo_desconocido(api: TestClient) -> None:
    assert verificar(api, "nadie@anuc.org").json() == {"existe": False}


def test_no_distingue_mayusculas_ni_espacios(api: TestClient, nueva_cuenta: NuevaCuenta) -> None:
    nueva_cuenta(Rol.TECNICO, correo="ana@anuc.org")
    assert verificar(api, "  ANA@Anuc.org ").json() == {"existe": True}


def test_una_cuenta_deshabilitada_tambien_se_reconoce(api: TestClient, nueva_cuenta: NuevaCuenta) -> None:
    nueva_cuenta(Rol.TECNICO, correo="ana@anuc.org", activo=False)
    assert verificar(api, "ana@anuc.org").json() == {"existe": True}


def test_solo_revela_si_existe(api: TestClient, nueva_cuenta: NuevaCuenta) -> None:
    nueva_cuenta(Rol.TECNICO, correo="ana@anuc.org")
    assert set(verificar(api, "ana@anuc.org").json()) == {"existe"}


def test_correo_mal_formado_422(api: TestClient) -> None:
    r = verificar(api, "esto no es un correo")
    assert r.status_code == 422 and "correo" in r.json()["campos"]


def test_no_requiere_sesion(api: TestClient) -> None:
    assert verificar(api, "x@anuc.org").status_code == 200


def test_el_sexto_intento_en_un_minuto_es_429(api: TestClient) -> None:
    codigos = [verificar(api, f"c{i}@anuc.org").status_code for i in range(6)]
    assert codigos == [200] * 5 + [429]
    r = verificar(api, "otro@anuc.org")
    assert r.json()["codigo"] == "DEMASIADAS_SOLICITUDES"
    assert 1 <= int(r.headers["retry-after"]) <= 61


def test_pasado_el_minuto_se_puede_volver_a_consultar(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    reloj = [1000.0]
    monkeypatch.setattr(LIMITADOR_VERIFICAR_CORREO, "_reloj", lambda: reloj[0])
    LIMITADOR_VERIFICAR_CORREO.reiniciar()
    for _ in range(5):
        verificar(api, "a@anuc.org")
    assert verificar(api, "a@anuc.org").status_code == 429
    reloj[0] += 61
    assert verificar(api, "a@anuc.org").status_code == 200


def test_el_limite_no_afecta_a_otros_endpoints(api: TestClient) -> None:
    for _ in range(7):
        verificar(api, "a@anuc.org")
    assert api.get(f"{URL}/estado").status_code == 200
