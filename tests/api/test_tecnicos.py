"""GET/POST /tecnicos, GET/PATCH /tecnicos/{id} y PATCH /tecnicos/{id}/estado (solo el líder técnico)."""

import asyncio
import logging

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.core.roles import Rol
from app.infrastructure.cognito_admin_client import CognitoSimulado, get_cognito_admin
from app.infrastructure.db.models import Tecnico
from app.infrastructure.db.seed import seed
from app.main import app
from app.modules.panel_api.cuentas import service as servicio
from tests.api.conftest import CognitoQueFalla, Cuenta, NuevaCuenta, auditoria_de, consultar
from tests.infrastructure.db.factories import asignar, caso_en, escenario

URL = "/api/v1/tecnicos"
NUEVO = {"nombre": "Beto Quintero", "correo": "beto@anuc.org", "celular": "3109998877", "rol": "TECNICO"}


@pytest.fixture
def lider(nueva_cuenta: NuevaCuenta) -> Cuenta:
    return nueva_cuenta(Rol.LIDER_TECNICO)


# --- permisos ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("metodo", "ruta", "cuerpo"),
    [
        ("GET", URL, None),
        ("POST", URL, NUEVO),
        ("GET", f"{URL}/TEC-000001", None),
        ("PATCH", f"{URL}/TEC-000001", {"nombre": "X"}),
        ("PATCH", f"{URL}/TEC-000001/estado", {"activo": False}),
    ],
)
def test_sin_sesion_401(api: TestClient, metodo: str, ruta: str, cuerpo: dict[str, str] | None) -> None:
    assert api.request(metodo, ruta, json=cuerpo).status_code == 401


@pytest.mark.parametrize(
    ("metodo", "ruta", "cuerpo"),
    [
        ("GET", URL, None),
        ("GET", f"{URL}?con_carga=true", None),
        ("POST", URL, NUEVO),
        ("GET", f"{URL}/TEC-000001", None),
        ("PATCH", f"{URL}/TEC-000001", {"nombre": "X"}),
        ("PATCH", f"{URL}/TEC-000001/estado", {"activo": False}),
    ],
)
def test_un_tecnico_no_administra_cuentas_403(
    api: TestClient, nueva_cuenta: NuevaCuenta, metodo: str, ruta: str, cuerpo: dict[str, str] | None
) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    r = api.request(metodo, ruta, json=cuerpo, headers=ana.headers)
    assert r.status_code == 403 and r.json()["codigo"] == "SIN_PERMISO"


# --- GET /tecnicos -----------------------------------------------------------------------------------------------


def test_lista_lideres_y_tecnicos_juntos_con_los_lideres_primero(
    api: TestClient, nueva_cuenta: NuevaCuenta, lider: Cuenta
) -> None:
    nueva_cuenta(Rol.TECNICO, 2, nombre="Zoila Ruiz")
    nueva_cuenta(Rol.TECNICO, 1, nombre="Ana Torres")
    nueva_cuenta(Rol.LIDER_TECNICO, 2, nombre="Abel Gómez")
    r = api.get(URL, headers=lider.headers)
    assert r.status_code == 200
    assert [(c["rol"], c["nombre"]) for c in r.json()["items"]] == [
        ("LIDER_TECNICO", "Abel Gómez"),
        ("LIDER_TECNICO", "Lider 1"),
        ("TECNICO", "Ana Torres"),
        ("TECNICO", "Zoila Ruiz"),
    ]
    assert (r.json()["total"], r.json()["page"], r.json()["page_size"]) == (4, 1, 20)


def test_cada_cuenta_trae_los_campos_del_contrato(api: TestClient, nueva_cuenta: NuevaCuenta, lider: Cuenta) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    cuenta = next(c for c in api.get(URL, headers=lider.headers).json()["items"] if c["id"] == ana.id)
    assert cuenta == {
        "id": ana.id,
        "nombre": ana.nombre,
        "correo": ana.correo,
        "celular": ana.celular,  # 10 dígitos
        "rol": "TECNICO",
        "activo": True,
        "casos_activos": 0,
        "casos_pendientes": 0,
        "unica_activa_del_rol": True,
    }


def test_no_expone_datos_internos(api: TestClient, nueva_cuenta: NuevaCuenta, lider: Cuenta) -> None:
    nueva_cuenta(Rol.TECNICO)
    cuerpo = api.get(URL, headers=lider.headers).text
    for oculto in ("cognito_sub", "contrasena", "hash-inutilizable", "573", lider.sub):
        assert oculto not in cuerpo


def test_filtra_por_rol(api: TestClient, nueva_cuenta: NuevaCuenta, lider: Cuenta) -> None:
    nueva_cuenta(Rol.TECNICO)
    solo = lambda rol: {c["rol"] for c in api.get(URL, params={"rol": rol}, headers=lider.headers).json()["items"]}  # noqa: E731
    assert solo("TECNICO") == {"TECNICO"} and solo("LIDER_TECNICO") == {"LIDER_TECNICO"}


def test_filtra_por_estado(api: TestClient, nueva_cuenta: NuevaCuenta, lider: Cuenta) -> None:
    nueva_cuenta(Rol.TECNICO, 1)
    nueva_cuenta(Rol.TECNICO, 2, activo=False)

    def nombres(activo: str) -> set[str]:
        r = api.get(URL, params={"activo": activo, "rol": "TECNICO"}, headers=lider.headers)
        return {c["nombre"] for c in r.json()["items"]}

    assert nombres("true") == {"Tecnico 1"} and nombres("false") == {"Tecnico 2"}


def test_filtra_por_estado_con_el_parametro_del_panel(
    api: TestClient, nueva_cuenta: NuevaCuenta, lider: Cuenta
) -> None:
    """El panel envía `estado=activa|deshabilitada` (contrato del frontend)."""
    nueva_cuenta(Rol.TECNICO, 1)
    nueva_cuenta(Rol.TECNICO, 2, activo=False)

    def nombres(estado: str) -> set[str]:
        r = api.get(URL, params={"estado": estado, "rol": "TECNICO"}, headers=lider.headers)
        return {c["nombre"] for c in r.json()["items"]}

    assert nombres("activa") == {"Tecnico 1"} and nombres("deshabilitada") == {"Tecnico 2"}


def test_busca_por_nombre_o_correo_sin_importar_tildes_ni_mayusculas(
    api: TestClient, nueva_cuenta: NuevaCuenta, lider: Cuenta
) -> None:
    nueva_cuenta(Rol.TECNICO, 1, nombre="María Pérez", correo="maria.p@anuc.org")
    nueva_cuenta(Rol.TECNICO, 2, nombre="Juan Gómez", correo="juan@anuc.org")
    buscar = lambda q: {c["nombre"] for c in api.get(URL, params={"q": q}, headers=lider.headers).json()["items"]}  # noqa: E731
    assert buscar("perez") == {"María Pérez"} and buscar("MARÍA") == {"María Pérez"}
    assert buscar("gomez") == {"Juan Gómez"} and buscar("juan@") == {"Juan Gómez"}
    assert buscar("zzz") == set()


def test_la_busqueda_no_es_inyectable(api: TestClient, lider: Cuenta) -> None:
    for q in ("'; DROP TABLE tecnicos; --", "%", "_", "\\"):
        assert api.get(URL, params={"q": q}, headers=lider.headers).status_code == 200


def test_pagina_y_cuenta_el_total(api: TestClient, nueva_cuenta: NuevaCuenta, lider: Cuenta) -> None:
    for n in range(1, 8):
        nueva_cuenta(Rol.TECNICO, n, nombre=f"Tec {n}")
    p1 = api.get(URL, params={"page": 1, "page_size": 3}, headers=lider.headers).json()
    p2 = api.get(URL, params={"page": 2, "page_size": 3}, headers=lider.headers).json()
    p3 = api.get(URL, params={"page": 3, "page_size": 3}, headers=lider.headers).json()
    assert (p1["total"], p2["total"], p3["total"]) == (8, 8, 8)
    assert [len(p["items"]) for p in (p1, p2, p3)] == [3, 3, 2]
    ids = [c["id"] for p in (p1, p2, p3) for c in p["items"]]
    assert len(ids) == len(set(ids)) == 8  # sin repetidos ni saltos


def test_una_pagina_fuera_de_rango_igual_informa_el_total(
    api: TestClient, nueva_cuenta: NuevaCuenta, lider: Cuenta
) -> None:
    nueva_cuenta(Rol.TECNICO)
    r = api.get(URL, params={"page": 9, "page_size": 5}, headers=lider.headers).json()
    assert r["items"] == [] and r["total"] == 2 and r["page"] == 9


@pytest.mark.parametrize(
    "parametros",
    [
        {"page": 0},
        {"page": -1},
        {"page_size": 0},
        {"page_size": 101},
        {"rol": "ADMIN"},
        {"carga": "mucha"},
        {"activo": "quizas"},
    ],
)
def test_parametros_invalidos_422(api: TestClient, lider: Cuenta, parametros: dict[str, object]) -> None:
    assert api.get(URL, params=parametros, headers=lider.headers).status_code == 422


def test_el_maximo_de_pagina_es_100(api: TestClient, lider: Cuenta) -> None:
    assert api.get(URL, params={"page_size": 100}, headers=lider.headers).json()["page_size"] == 100


def test_unica_activa_del_rol(api: TestClient, nueva_cuenta: NuevaCuenta, lider: Cuenta) -> None:
    ana, beto = nueva_cuenta(Rol.TECNICO, 1), nueva_cuenta(Rol.TECNICO, 2)
    por_id = lambda: {c["id"]: c for c in api.get(URL, headers=lider.headers).json()["items"]}  # noqa: E731
    assert por_id()[lider.id]["unica_activa_del_rol"] is True  # el único líder
    assert por_id()[ana.id]["unica_activa_del_rol"] is False and por_id()[beto.id]["unica_activa_del_rol"] is False
    api.patch(f"{URL}/{beto.id}/estado", json={"activo": False}, headers=lider.headers)
    assert por_id()[ana.id]["unica_activa_del_rol"] is True  # ahora es la única activa
    assert por_id()[beto.id]["unica_activa_del_rol"] is False  # la deshabilitada nunca lo es


# --- carga de casos ----------------------------------------------------------------------------------------------


@pytest.fixture
def con_casos(migrated: Engine, nueva_cuenta: NuevaCuenta) -> dict[str, Cuenta]:
    """Ana: 2 En revisión y 1 Registrado. Beto: 1 Cerrado. Cata: ninguno. Luis (técnico del líder): 1 En revisión."""
    from sqlalchemy.orm import Session

    with migrated.begin() as c:
        seed(c)
    cuentas = {
        "ana": nueva_cuenta(Rol.TECNICO, 1, nombre="Ana Torres"),
        "beto": nueva_cuenta(Rol.TECNICO, 2, nombre="Beto Quintero"),
        "cata": nueva_cuenta(Rol.TECNICO, 3, nombre="Cata Díaz"),
        "luis": nueva_cuenta(Rol.TECNICO, 4, nombre="Luis (técnico)"),
    }
    with Session(migrated) as s:
        datos = escenario(s)
        for clave, estado in (
            ("ana", "En revisión"),
            ("ana", "En revisión"),
            ("ana", "Registrado"),
            ("beto", "Cerrado"),
            ("luis", "En revisión"),
        ):
            asignar(s, caso_en(s, datos, estado), s.get(Tecnico, cuentas[clave].id))  # type: ignore[arg-type]
        # una asignación ya cerrada (reasignado) no cuenta como carga
        antiguo = caso_en(s, datos, "En revisión")
        asignar(s, antiguo, s.get(Tecnico, cuentas["cata"].id), vigente=False)  # type: ignore[arg-type]
        s.commit()
    return cuentas


def test_cuenta_los_casos_activos_y_pendientes(api: TestClient, lider: Cuenta, con_casos: dict[str, Cuenta]) -> None:
    por_id = {c["id"]: c for c in api.get(URL, headers=lider.headers).json()["items"]}
    esperado = {"ana": (3, 2), "beto": (0, 0), "cata": (0, 0), "luis": (1, 1)}  # (activos, pendientes)
    for clave, (activos, pendientes) in esperado.items():
        cuenta = por_id[con_casos[clave].id]
        assert (cuenta["casos_activos"], cuenta["casos_pendientes"]) == (activos, pendientes), clave
    assert (por_id[lider.id]["casos_activos"], por_id[lider.id]["casos_pendientes"]) == (0, 0)


def test_filtra_por_carga(api: TestClient, lider: Cuenta, con_casos: dict[str, Cuenta]) -> None:
    def ids(carga: str) -> set[str]:
        r = api.get(URL, params={"carga": carga, "rol": "TECNICO"}, headers=lider.headers)
        return {c["id"] for c in r.json()["items"]}

    assert ids("pendientes") == {con_casos["ana"].id, con_casos["luis"].id}
    assert ids("sin") == {con_casos["beto"].id, con_casos["cata"].id}


def test_con_carga_lista_solo_tecnicos_activos_para_asignar(
    api: TestClient, nueva_cuenta: NuevaCuenta, lider: Cuenta, con_casos: dict[str, Cuenta]
) -> None:
    nueva_cuenta(Rol.TECNICO, 9, nombre="Inactivo", activo=False)
    r = api.get(URL, params={"con_carga": "true"}, headers=lider.headers)
    assert r.status_code == 200 and isinstance(r.json(), list)  # no es paginada
    assert [(c["nombre"], c["activos"], c["en_revision"]) for c in r.json()] == [
        ("Ana Torres", 3, 2),
        ("Beto Quintero", 0, 0),
        ("Cata Díaz", 0, 0),
        ("Luis (técnico)", 1, 1),
    ]
    assert {c["rol"] for c in r.json()} == {"TECNICO"}  # el líder nunca recibe casos (D-02)
    assert set(r.json()[0]) == {"id", "nombre", "rol", "activos", "en_revision"}


# --- POST /tecnicos ----------------------------------------------------------------------------------------------


def test_crea_un_tecnico(api: TestClient, lider: Cuenta, migrated: Engine, cognito: CognitoSimulado) -> None:
    r = api.post(URL, json=NUEVO, headers=lider.headers)
    assert r.status_code == 201
    cuenta = r.json()
    assert cuenta["id"].startswith("TEC-") and cuenta["rol"] == "TECNICO" and cuenta["activo"] is True
    assert (cuenta["nombre"], cuenta["correo"], cuenta["celular"]) == ("Beto Quintero", "beto@anuc.org", "3109998877")
    assert (cuenta["casos_activos"], cuenta["casos_pendientes"]) == (0, 0)
    fila = consultar(migrated, "SELECT * FROM tecnicos WHERE id_tecnico = :i", i=cuenta["id"])[0]
    assert fila["celular_tecnico"] == "573109998877" and fila["estado_cuenta_tecnico"] == "ACTIVO"
    assert fila["cognito_sub"] == cognito.obtener("beto@anuc.org").sub


def test_el_tecnico_nuevo_recibe_clave_temporal_de_cognito_y_el_backend_no_la_conoce(
    api: TestClient, lider: Cuenta, migrated: Engine, cognito: CognitoSimulado
) -> None:
    api.post(URL, json=NUEVO, headers=lider.headers)
    assert cognito.obtener("beto@anuc.org").contrasena is None  # sin contraseña: Cognito envía la temporal por correo
    fila = consultar(migrated, "SELECT contrasena_tecnico FROM tecnicos WHERE correo_tecnico = 'beto@anuc.org'")[0]
    assert fila["contrasena_tecnico"].startswith("$argon2id$")  # hash inutilizable, no una clave real


def test_crea_otro_lider(api: TestClient, lider: Cuenta, migrated: Engine) -> None:
    r = api.post(URL, json={**NUEVO, "rol": "LIDER_TECNICO", "correo": "luis2@anuc.org"}, headers=lider.headers)
    assert r.status_code == 201 and r.json()["id"].startswith("LID-") and r.json()["rol"] == "LIDER_TECNICO"
    assert len(consultar(migrated, "SELECT 1 FROM lideres_tecnicos")) == 2


def test_audita_la_creacion_con_el_lider_como_autor(api: TestClient, lider: Cuenta, migrated: Engine) -> None:
    id_nuevo = api.post(URL, json=NUEVO, headers=lider.headers).json()["id"]
    [registro] = auditoria_de(migrated)
    assert (registro["origen"], registro["id_lider_tecnico"], registro["accion"]) == ("LIDER", lider.id, "CREAR_CUENTA")
    assert (registro["entidad_afectada"], registro["resultado"]) == ("tecnicos", "EXITO")
    assert id_nuevo in registro["descripcion"] and "3109998877" not in " ".join(map(str, registro.values()))


def test_el_correo_se_normaliza(api: TestClient, lider: Cuenta, cognito: CognitoSimulado) -> None:
    r = api.post(URL, json={**NUEVO, "correo": "  BETO@Anuc.ORG "}, headers=lider.headers)
    assert r.json()["correo"] == "beto@anuc.org" and cognito.existe("beto@anuc.org")


def test_correo_repetido_en_otro_tecnico_409(api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta) -> None:
    nueva_cuenta(Rol.TECNICO, correo="beto@anuc.org")
    r = api.post(URL, json=NUEVO, headers=lider.headers)
    assert r.status_code == 409 and r.json()["codigo"] == "DUPLICADO"
    assert r.json()["campos"] == {"correo": "Este correo ya está registrado"}


def test_el_correo_es_unico_entre_lideres_y_tecnicos(api: TestClient, lider: Cuenta) -> None:
    """Cognito identifica por correo: una cuenta de técnico no puede usar el correo de un líder."""
    r = api.post(URL, json={**NUEVO, "correo": lider.correo}, headers=lider.headers)
    assert r.status_code == 409 and "correo" in r.json()["campos"]


def test_celular_repetido_entre_tecnicos_409(api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta) -> None:
    otro = nueva_cuenta(Rol.TECNICO)
    r = api.post(URL, json={**NUEVO, "celular": otro.celular}, headers=lider.headers)
    assert r.status_code == 409 and r.json()["campos"] == {"celular": "Este número de celular ya está registrado"}


def test_correo_y_celular_repetidos_se_informan_juntos(
    api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta
) -> None:
    otro = nueva_cuenta(Rol.TECNICO)
    r = api.post(URL, json={**NUEVO, "correo": otro.correo, "celular": otro.celular}, headers=lider.headers)
    assert set(r.json()["campos"]) == {"correo", "celular"}


def test_la_cuenta_de_tecnico_del_lider_puede_usar_su_celular(api: TestClient, lider: Cuenta) -> None:
    """D-02: el líder atiende casos con otra cuenta (otro correo, misma persona)."""
    r = api.post(
        URL, json={**NUEVO, "correo": "luis.tecnico@anuc.org", "celular": lider.celular}, headers=lider.headers
    )
    assert r.status_code == 201


@pytest.mark.parametrize(
    ("cambio", "campo"),
    [
        ({"nombre": ""}, "nombre"),
        ({"correo": "malo"}, "correo"),
        ({"celular": "2109998877"}, "celular"),
        ({"rol": "ADMIN"}, "rol"),
    ],
)
def test_datos_invalidos_422(
    api: TestClient, lider: Cuenta, cognito: CognitoSimulado, cambio: dict[str, str], campo: str
) -> None:
    r = api.post(URL, json={**NUEVO, **cambio}, headers=lider.headers)
    assert r.status_code == 422 and campo in r.json()["campos"]
    assert not cognito.existe("beto@anuc.org")


def test_acepta_el_celular_en_e164_como_lo_envia_el_panel(api: TestClient, lider: Cuenta) -> None:
    r = api.post(URL, json={**NUEVO, "celular": "+573109998877"}, headers=lider.headers)
    assert r.status_code == 201 and r.json()["celular"] == "3109998877"


def test_faltan_campos(api: TestClient, lider: Cuenta) -> None:
    assert set(api.post(URL, json={}, headers=lider.headers).json()["campos"]) == {"nombre", "correo", "celular", "rol"}


def test_si_cognito_falla_no_queda_la_cuenta(api: TestClient, lider: Cuenta, migrated: Engine) -> None:
    app.dependency_overrides[get_cognito_admin] = lambda: CognitoQueFalla("crear")
    r = api.post(URL, json=NUEVO, headers=lider.headers)
    assert r.status_code == 503 and r.json()["codigo"] == "ERROR_INTERNO"
    assert consultar(migrated, "SELECT 1 FROM tecnicos") == [] and auditoria_de(migrated) == []


def test_si_la_base_falla_se_elimina_el_usuario_de_cognito(
    api: TestClient, lider: Cuenta, cognito: CognitoSimulado, migrated: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def explota(*_a: object, **_k: object) -> str:
        raise RuntimeError("base caída")

    monkeypatch.setattr(servicio.repo, "crear", explota)
    assert api.post(URL, json=NUEVO, headers=lider.headers).status_code == 500
    assert cognito.eliminados == ["beto@anuc.org"] and not cognito.existe("beto@anuc.org")
    assert consultar(migrated, "SELECT 1 FROM tecnicos") == []


def test_si_otra_solicitud_se_adelanta_con_el_mismo_correo_no_quedan_dos(
    api: TestClient, lider: Cuenta, migrated: Engine, cognito: CognitoSimulado
) -> None:
    async def disparar() -> list[httpx.Response]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as cliente:
            return await asyncio.gather(
                *(
                    cliente.post(URL, json={**NUEVO, "celular": f"31099988{i}0"}, headers=lider.headers)
                    for i in range(3)
                )
            )

    codigos = sorted(r.status_code for r in asyncio.run(disparar()))
    assert codigos == [201, 409, 409]
    assert len(consultar(migrated, "SELECT 1 FROM tecnicos")) == 1


# --- GET /tecnicos/{id} ------------------------------------------------------------------------------------------


def test_obtiene_una_cuenta(api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    r = api.get(f"{URL}/{ana.id}", headers=lider.headers)
    assert r.status_code == 200 and r.json()["id"] == ana.id and r.json()["correo"] == ana.correo


def test_obtiene_al_lider_por_su_id(api: TestClient, lider: Cuenta) -> None:
    r = api.get(f"{URL}/{lider.id}", headers=lider.headers)
    assert r.status_code == 200 and r.json()["rol"] == "LIDER_TECNICO" and r.json()["id"].startswith("LID-")


@pytest.mark.parametrize("id_cuenta", ["TEC-999999", "LID-999999", "XYZ-000001", "tec-000001", "1", "%20", "TEC-"])
def test_cuenta_inexistente_404(api: TestClient, lider: Cuenta, id_cuenta: str) -> None:
    r = api.get(f"{URL}/{id_cuenta}", headers=lider.headers)
    assert r.status_code == 404 and r.json()["codigo"] == "NO_ENCONTRADO"


# --- PATCH /tecnicos/{id} ----------------------------------------------------------------------------------------


def test_edita_los_datos_de_un_tecnico(
    api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta, migrated: Engine, cognito: CognitoSimulado
) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    r = api.patch(
        f"{URL}/{ana.id}",
        json={"nombre": "Ana María", "correo": "ana.maria@anuc.org", "celular": "3201112233", "rol": "TECNICO"},
        headers=lider.headers,
    )
    assert r.status_code == 200
    assert (r.json()["nombre"], r.json()["correo"], r.json()["celular"]) == (
        "Ana María",
        "ana.maria@anuc.org",
        "3201112233",
    )
    fila = consultar(migrated, "SELECT * FROM tecnicos")[0]
    assert (fila["nombre_tecnico"], fila["correo_tecnico"], fila["celular_tecnico"]) == (
        "Ana María",
        "ana.maria@anuc.org",
        "573201112233",
    )
    usuario = cognito.obtener("ana.maria@anuc.org")  # el correo es el usuario de Cognito: se cambió allí también
    assert (usuario.nombre, usuario.celular) == ("Ana María", "573201112233") and not cognito.existe(ana.correo)


def test_edita_un_solo_campo(api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    r = api.patch(f"{URL}/{ana.id}", json={"celular": "3201112233"}, headers=lider.headers)
    assert (r.json()["nombre"], r.json()["correo"], r.json()["celular"]) == (ana.nombre, ana.correo, "3201112233")


def test_conserva_su_identidad_en_cognito(
    api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta, cognito: CognitoSimulado, migrated: Engine
) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    api.patch(f"{URL}/{ana.id}", json={"correo": "nuevo@anuc.org"}, headers=lider.headers)
    assert (
        cognito.obtener("nuevo@anuc.org").sub
        == ana.sub
        == consultar(migrated, "SELECT cognito_sub FROM tecnicos")[0]["cognito_sub"]
    )


def test_el_lider_no_se_edita_desde_aqui(api: TestClient, lider: Cuenta) -> None:
    """P-11: los datos del líder se editan desde Mi cuenta."""
    r = api.patch(f"{URL}/{lider.id}", json={"nombre": "Otro"}, headers=lider.headers)
    assert r.status_code == 403 and r.json()["codigo"] == "SIN_PERMISO"
    assert "Mi cuenta" in r.json()["mensaje"]


def test_no_se_puede_cambiar_el_rol(api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    r = api.patch(f"{URL}/{ana.id}", json={"rol": "LIDER_TECNICO"}, headers=lider.headers)
    assert r.status_code == 422 and r.json()["campos"] == {"rol": "No se puede cambiar el rol de una cuenta"}


def test_enviar_el_mismo_rol_es_valido(api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    assert (
        api.patch(f"{URL}/{ana.id}", json={"rol": "TECNICO", "nombre": "Ana B."}, headers=lider.headers).status_code
        == 200
    )


def test_correo_de_otro_409(api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta, migrated: Engine) -> None:
    ana, beto = nueva_cuenta(Rol.TECNICO, 1), nueva_cuenta(Rol.TECNICO, 2)
    r = api.patch(f"{URL}/{ana.id}", json={"correo": beto.correo}, headers=lider.headers)
    assert r.status_code == 409 and r.json()["campos"] == {"correo": "Este correo ya está registrado"}
    assert auditoria_de(migrated) == []


def test_correo_del_lider_tampoco_se_puede_tomar(api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    assert api.patch(f"{URL}/{ana.id}", json={"correo": lider.correo}, headers=lider.headers).status_code == 409


def test_celular_de_otro_409(api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta) -> None:
    ana, beto = nueva_cuenta(Rol.TECNICO, 1), nueva_cuenta(Rol.TECNICO, 2)
    r = api.patch(f"{URL}/{ana.id}", json={"celular": beto.celular}, headers=lider.headers)
    assert r.status_code == 409 and "celular" in r.json()["campos"]


def test_conservar_sus_propios_datos_no_es_duplicado(
    api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta, migrated: Engine
) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    r = api.patch(
        f"{URL}/{ana.id}",
        json={"correo": ana.correo.upper(), "celular": ana.celular, "nombre": ana.nombre},
        headers=lider.headers,
    )
    assert r.status_code == 200 and auditoria_de(migrated) == []  # sin cambios reales: no audita


def test_edicion_invalida_422(api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    r = api.patch(f"{URL}/{ana.id}", json={"correo": "malo", "celular": "1"}, headers=lider.headers)
    assert r.status_code == 422 and set(r.json()["campos"]) == {"correo", "celular"}


def test_editar_una_cuenta_inexistente_404(api: TestClient, lider: Cuenta) -> None:
    assert api.patch(f"{URL}/TEC-999999", json={"nombre": "X"}, headers=lider.headers).status_code == 404


def test_audita_la_edicion_sin_el_celular(
    api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta, migrated: Engine
) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    api.patch(f"{URL}/{ana.id}", json={"nombre": "Ana Nueva", "celular": "3201112233"}, headers=lider.headers)
    [registro] = auditoria_de(migrated)
    assert (registro["origen"], registro["id_lider_tecnico"], registro["accion"]) == (
        "LIDER",
        lider.id,
        "EDITAR_CUENTA",
    )
    assert ana.nombre in registro["valor_anterior"] and "Ana Nueva" in registro["valor_nuevo"]
    assert "3201112233" not in " ".join(map(str, registro.values()))


def test_si_cognito_falla_la_edicion_no_se_aplica(
    api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta, migrated: Engine
) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    app.dependency_overrides[get_cognito_admin] = lambda: CognitoQueFalla("actualizar")
    r = api.patch(f"{URL}/{ana.id}", json={"nombre": "Otro", "correo": "otro@anuc.org"}, headers=lider.headers)
    assert r.status_code == 503
    fila = consultar(migrated, "SELECT nombre_tecnico, correo_tecnico FROM tecnicos")[0]
    assert (fila["nombre_tecnico"], fila["correo_tecnico"]) == (ana.nombre, ana.correo) and auditoria_de(migrated) == []


# --- PATCH /tecnicos/{id}/estado ---------------------------------------------------------------------------------


def estado(api: TestClient, lider: Cuenta, id_cuenta: str, activo: bool) -> httpx.Response:
    return api.patch(f"{URL}/{id_cuenta}/estado", json={"activo": activo}, headers=lider.headers)


def test_deshabilita_un_tecnico(
    api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta, migrated: Engine, cognito: CognitoSimulado
) -> None:
    ana, _beto = nueva_cuenta(Rol.TECNICO, 1), nueva_cuenta(Rol.TECNICO, 2)
    r = estado(api, lider, ana.id, False)
    assert r.status_code == 200 and r.json()["activo"] is False and r.json()["id"] == ana.id
    assert (
        consultar(migrated, "SELECT estado_cuenta_tecnico FROM tecnicos WHERE id_tecnico = :i", i=ana.id)[0][
            "estado_cuenta_tecnico"
        ]
        == "DESHABILITADO"
    )
    assert not cognito.obtener(ana.correo).habilitado  # y no puede iniciar sesión


def test_vuelve_a_habilitarlo(
    api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta, cognito: CognitoSimulado
) -> None:
    ana = nueva_cuenta(Rol.TECNICO, 1)
    nueva_cuenta(Rol.TECNICO, 2)
    estado(api, lider, ana.id, False)
    r = estado(api, lider, ana.id, True)
    assert r.json()["activo"] is True and cognito.obtener(ana.correo).habilitado


def test_habilitar_una_cuenta_ya_habilitada_es_idempotente(
    api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta, migrated: Engine
) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    assert estado(api, lider, ana.id, True).status_code == 200
    assert auditoria_de(migrated) == []


def test_deshabilitar_dos_veces_es_idempotente(
    api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta, migrated: Engine
) -> None:
    ana, _ = nueva_cuenta(Rol.TECNICO, 1), nueva_cuenta(Rol.TECNICO, 2)
    estado(api, lider, ana.id, False)
    assert estado(api, lider, ana.id, False).status_code == 200
    assert len(auditoria_de(migrated)) == 1  # la segunda vez no audita


def test_la_unica_cuenta_activa_de_un_rol_no_se_puede_deshabilitar(
    api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta, migrated: Engine, cognito: CognitoSimulado
) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    r = estado(api, lider, ana.id, False)
    assert r.status_code == 409 and r.json()["codigo"] == "UNICA_CUENTA_ACTIVA"
    assert cognito.obtener(ana.correo).habilitado and auditoria_de(migrated) == []
    assert consultar(migrated, "SELECT estado_cuenta_tecnico FROM tecnicos")[0]["estado_cuenta_tecnico"] == "ACTIVO"


def test_ni_el_unico_lider_se_puede_deshabilitar_a_si_mismo(api: TestClient, lider: Cuenta) -> None:
    r = estado(api, lider, lider.id, False)
    assert r.status_code == 409 and r.json()["codigo"] == "UNICA_CUENTA_ACTIVA"


def test_con_dos_lideres_uno_se_puede_deshabilitar(api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta) -> None:
    otro = nueva_cuenta(Rol.LIDER_TECNICO, 2)
    assert estado(api, lider, otro.id, False).status_code == 200
    assert estado(api, lider, lider.id, False).status_code == 409  # y ahora el primero es el único


def test_los_roles_se_cuentan_por_separado(api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta) -> None:
    """Hay un líder activo, pero la única técnica sigue protegida por su propio rol."""
    ana = nueva_cuenta(Rol.TECNICO)
    assert estado(api, lider, ana.id, False).status_code == 409


def test_una_cuenta_ya_deshabilitada_no_cuenta_como_activa(
    api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta
) -> None:
    ana, beto = nueva_cuenta(Rol.TECNICO, 1), nueva_cuenta(Rol.TECNICO, 2)
    nueva_cuenta(Rol.TECNICO, 3, activo=False)
    estado(api, lider, beto.id, False)
    assert estado(api, lider, ana.id, False).status_code == 409  # solo ana queda activa


def test_una_cuenta_deshabilitada_no_puede_entrar(api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta) -> None:
    ana, _ = nueva_cuenta(Rol.TECNICO, 1), nueva_cuenta(Rol.TECNICO, 2)
    assert api.get("/api/v1/me", headers=ana.headers).status_code == 200
    estado(api, lider, ana.id, False)
    r = api.get("/api/v1/me", headers=ana.headers)
    assert r.status_code == 403 and r.json()["codigo"] == "CUENTA_INACTIVA"
    estado(api, lider, ana.id, True)
    assert api.get("/api/v1/me", headers=ana.headers).status_code == 200


def test_audita_el_cambio_de_estado(
    api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta, migrated: Engine
) -> None:
    ana, _ = nueva_cuenta(Rol.TECNICO, 1), nueva_cuenta(Rol.TECNICO, 2)
    estado(api, lider, ana.id, False)
    estado(api, lider, ana.id, True)
    deshabilitar, habilitar = auditoria_de(migrated)
    assert deshabilitar["accion"] == habilitar["accion"] == "CAMBIAR_ESTADO"
    assert (deshabilitar["origen"], deshabilitar["id_lider_tecnico"]) == ("LIDER", lider.id)
    assert "deshabilitada" in deshabilitar["descripcion"] and "habilitada" in habilitar["descripcion"]
    assert '"activo": true' in deshabilitar["valor_anterior"] and '"activo": false' in deshabilitar["valor_nuevo"]


def test_cambiar_estado_de_cuenta_inexistente_404(api: TestClient, lider: Cuenta) -> None:
    assert estado(api, lider, "TEC-999999", False).status_code == 404


def test_el_cuerpo_exige_activo_booleano(api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    for cuerpo in ({}, {"activo": "tal vez"}, {"activo": None}):
        assert api.patch(f"{URL}/{ana.id}/estado", json=cuerpo, headers=lider.headers).status_code == 422


def test_si_cognito_falla_el_estado_no_cambia(
    api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta, migrated: Engine
) -> None:
    ana, _ = nueva_cuenta(Rol.TECNICO, 1), nueva_cuenta(Rol.TECNICO, 2)
    app.dependency_overrides[get_cognito_admin] = lambda: CognitoQueFalla("deshabilitar")
    r = estado(api, lider, ana.id, False)
    assert r.status_code == 503 and r.json()["codigo"] == "ERROR_INTERNO"
    assert (
        consultar(migrated, "SELECT estado_cuenta_tecnico FROM tecnicos WHERE id_tecnico = :i", i=ana.id)[0][
            "estado_cuenta_tecnico"
        ]
        == "ACTIVO"
    )
    assert auditoria_de(migrated) == []


def test_si_la_base_falla_se_revierte_cognito(
    api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta, cognito: CognitoSimulado, monkeypatch: pytest.MonkeyPatch
) -> None:
    ana, _ = nueva_cuenta(Rol.TECNICO, 1), nueva_cuenta(Rol.TECNICO, 2)

    async def explota(*_a: object, **_k: object) -> None:
        raise RuntimeError("base caída")

    monkeypatch.setattr(servicio.repo, "cambiar_estado", explota)
    assert estado(api, lider, ana.id, False).status_code == 500
    assert cognito.obtener(ana.correo).habilitado  # se volvió a habilitar: base y Cognito coinciden


def test_dos_deshabilitaciones_simultaneas_no_dejan_el_rol_sin_cuentas(
    api: TestClient, lider: Cuenta, nueva_cuenta: NuevaCuenta, migrated: Engine
) -> None:
    """Las dos únicas técnicas activas se deshabilitan a la vez: el candado deja pasar a una sola."""
    ana, beto = nueva_cuenta(Rol.TECNICO, 1), nueva_cuenta(Rol.TECNICO, 2)

    async def disparar() -> list[httpx.Response]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as cliente:
            return await asyncio.gather(
                *(
                    cliente.patch(f"{URL}/{c.id}/estado", json={"activo": False}, headers=lider.headers)
                    for c in (ana, beto)
                )
            )

    respuestas = asyncio.run(disparar())
    assert sorted(r.status_code for r in respuestas) == [200, 409]
    assert [r.json()["codigo"] for r in respuestas if r.status_code == 409] == ["UNICA_CUENTA_ACTIVA"]
    activas = consultar(migrated, "SELECT count(*) AS n FROM tecnicos WHERE estado_cuenta_tecnico = 'ACTIVO'")
    assert activas[0]["n"] == 1


# --- registros ---------------------------------------------------------------------------------------------------


def test_nada_de_esto_deja_contrasenas_en_los_logs(
    api: TestClient, lider: Cuenta, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        api.post(URL, json=NUEVO, headers=lider.headers)
    assert "Authorization" not in caplog.text and "Bearer" not in caplog.text
    assert "3109998877" not in caplog.text  # ni el celular completo
