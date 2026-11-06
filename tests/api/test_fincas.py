"""/fincas, /cultivos y los catálogos (/especies, /categorias, /veredas)."""

import asyncio
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.core.roles import Rol
from app.main import app
from tests.api.conftest import Cuenta, NuevaCuenta, auditoria_de, consultar
from tests.api.territorio import FINCAS as URL
from tests.api.territorio import crear_caso, crear_cultivo, crear_finca, crear_productor

CULTIVOS = "/api/v1/cultivos"


@pytest.fixture
def lider(nueva_cuenta: NuevaCuenta) -> Cuenta:
    return nueva_cuenta(Rol.LIDER_TECNICO)


def cuerpo(id_productor: str, **extra: Any) -> dict[str, Any]:
    return {
        "nombre": "La Esperanza",
        "vereda": "Centro",
        "latitud": 5.25,
        "longitud": -73.17,
        "productor_id": id_productor,
        **extra,
    }


# --- permisos ----------------------------------------------------------------------------------------------------

RUTAS = [
    ("GET", URL, None),
    ("POST", URL, {"nombre": "X"}),
    ("GET", f"{URL}/FIN-000001", None),
    ("PATCH", f"{URL}/FIN-000001", {"nombre": "X"}),
    ("GET", f"{URL}/FIN-000001/cultivos", None),
    ("POST", f"{URL}/FIN-000001/cultivos", {"nombre": "X", "tipo": "INVERNADERO", "especies": ["GULUPA"]}),
    ("PATCH", f"{CULTIVOS}/CUL-000001", {"nombre": "X"}),
]


@pytest.mark.parametrize(("metodo", "ruta", "datos"), RUTAS)
def test_sin_sesion_401(api: TestClient, metodo: str, ruta: str, datos: dict[str, Any] | None) -> None:
    assert api.request(metodo, ruta, json=datos).status_code == 401


@pytest.mark.parametrize(("metodo", "ruta", "datos"), RUTAS)
def test_el_tecnico_no_administra_fincas_403(
    api: TestClient, nueva_cuenta: NuevaCuenta, metodo: str, ruta: str, datos: dict[str, Any] | None
) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    r = api.request(metodo, ruta, json=datos, headers=ana.headers)
    assert r.status_code == 403 and r.json()["codigo"] == "SIN_PERMISO"


# --- POST /fincas ------------------------------------------------------------------------------------------------


def test_crea_una_finca(api: TestClient, lider: Cuenta, catalogos: None, migrated: Engine) -> None:
    p = crear_productor(api, lider)
    r = api.post(URL, json=cuerpo(p["id"]), headers=lider.headers)
    assert r.status_code == 201
    f = r.json()
    assert f["id"].startswith("FIN-") and (f["nombre"], f["vereda"], f["latitud"], f["longitud"]) == (
        "La Esperanza",
        "CENTRO",
        5.25,
        -73.17,
    )
    assert f["productor"] == {"id": p["id"], "nombre": p["nombre"], "activo": True}
    assert (f["cultivos"], f["casos_activos"], f["advertencias"]) == ([], 0, [])
    fila = consultar(migrated, "SELECT * FROM fincas")[0]
    assert fila["vereda_sector_finca"] == "Centro" and fila["ubicacion"] is not None  # la columna generada se llena


def test_las_coordenadas_se_guardan_con_seis_decimales(api: TestClient, lider: Cuenta, catalogos: None) -> None:
    p = crear_productor(api, lider)
    f = api.post(URL, json=cuerpo(p["id"], latitud=5.2834567891, longitud=-73.1712345678), headers=lider.headers).json()
    assert (f["latitud"], f["longitud"]) == (5.283457, -73.171235)


def test_acepta_la_vereda_como_codigo_y_la_guarda_con_nombre(
    api: TestClient, lider: Cuenta, catalogos: None, migrated: Engine
) -> None:
    p = crear_productor(api, lider)
    f = api.post(URL, json=cuerpo(p["id"], vereda="SAN_LUIS"), headers=lider.headers).json()
    assert f["vereda"] == "SAN_LUIS"
    assert consultar(migrated, "SELECT vereda_sector_finca AS v FROM fincas")[0]["v"] == "San Luis"


def test_reutiliza_la_grafia_de_una_vereda_ya_registrada(
    api: TestClient, lider: Cuenta, catalogos: None, migrated: Engine
) -> None:
    p = crear_productor(api, lider)
    crear_finca(api, lider, p["id"], "Primera", "Juracambita")
    crear_finca(api, lider, p["id"], "Segunda", "JURACAMBITA")
    crear_finca(api, lider, p["id"], "Tercera", " juracambita ")
    assert {f["v"] for f in consultar(migrated, "SELECT vereda_sector_finca AS v FROM fincas")} == {"Juracambita"}


def test_solo_se_registra_finca_para_un_productor_activo(
    api: TestClient, lider: Cuenta, catalogos: None, migrated: Engine
) -> None:
    p = crear_productor(api, lider)
    api.patch(f"/api/v1/productores/{p['id']}/estado", json={"activo": False}, headers=lider.headers)
    r = api.post(URL, json=cuerpo(p["id"]), headers=lider.headers)
    assert r.status_code == 422 and r.json()["campos"] == {
        "productor_id": "Solo se registran fincas de productores activos."
    }
    assert consultar(migrated, "SELECT 1 FROM fincas") == []


@pytest.mark.parametrize("id_productor", ["PRO-999999", "x", "TEC-000001"])
def test_productor_inexistente_422(api: TestClient, lider: Cuenta, catalogos: None, id_productor: str) -> None:
    r = api.post(URL, json=cuerpo(id_productor), headers=lider.headers)
    assert r.status_code == 422 and r.json()["campos"] == {"productor_id": "Selecciona el productor."}


@pytest.mark.parametrize(
    ("cambio", "campo"),
    [
        ({"nombre": ""}, "nombre"),
        ({"vereda": ""}, "vereda"),
        ({"vereda": "---"}, "vereda"),
        ({"latitud": 95}, "latitud"),
        ({"longitud": -181}, "longitud"),
        ({"latitud": "cinco"}, "latitud"),
    ],
)
def test_datos_invalidos_422(
    api: TestClient, lider: Cuenta, catalogos: None, cambio: dict[str, Any], campo: str
) -> None:
    p = crear_productor(api, lider)
    r = api.post(URL, json=cuerpo(p["id"], **cambio), headers=lider.headers)
    assert r.status_code == 422 and campo in r.json()["campos"]


def test_faltan_campos(api: TestClient, lider: Cuenta) -> None:
    campos = api.post(URL, json={}, headers=lider.headers).json()["campos"]
    assert set(campos) == {"nombre", "vereda", "latitud", "longitud", "productor_id"}


def test_coordenadas_fuera_de_la_zona_se_aceptan_con_advertencia(
    api: TestClient, lider: Cuenta, catalogos: None
) -> None:
    p = crear_productor(api, lider)
    r = api.post(URL, json=cuerpo(p["id"], latitud=4.0, longitud=73.17), headers=lider.headers)
    assert r.status_code == 201 and len(r.json()["advertencias"]) == 2


def test_audita_el_alta(api: TestClient, lider: Cuenta, catalogos: None, migrated: Engine) -> None:
    p = crear_productor(api, lider)
    id_finca = crear_finca(api, lider, p["id"])["id"]
    registro = auditoria_de(migrated)[-1]
    assert (registro["accion"], registro["entidad_afectada"], registro["origen"]) == ("CREAR_FINCA", "fincas", "LIDER")
    assert id_finca in registro["descripcion"] and p["id"] in registro["descripcion"]


# --- GET /fincas/{id} --------------------------------------------------------------------------------------------


def test_obtiene_la_finca_con_sus_cultivos_y_casos_activos(
    api: TestClient, lider: Cuenta, catalogos: None, migrated: Engine
) -> None:
    p = crear_productor(api, lider)
    finca = crear_finca(api, lider, p["id"])
    con_cultivos = crear_cultivo(api, lider, finca["id"], "Lote 1", "INVERNADERO", ("MARACUYA", "GULUPA"))
    id_cultivo = con_cultivos["cultivos"][0]["id"]
    activo = crear_caso(migrated, id_cultivo, "GULUPA", "Registrado")
    crear_caso(migrated, id_cultivo, "GULUPA", "Cerrado")
    crear_caso(migrated, id_cultivo, "MARACUYA", "Pendiente de revisión")
    r = api.get(f"{URL}/{finca['id']}", headers=lider.headers).json()
    assert r["casos_activos"] == 2
    [cultivo] = r["cultivos"]
    assert (cultivo["nombre"], cultivo["tipo"], cultivo["especies"]) == (
        "Lote 1",
        "INVERNADERO",
        ["GULUPA", "MARACUYA"],
    )
    assert activo in cultivo["casos_activos"] and len(cultivo["casos_activos"]) == 2


@pytest.mark.parametrize("id_finca", ["FIN-999999", "PRO-000001", "x"])
def test_finca_inexistente_404(api: TestClient, lider: Cuenta, id_finca: str) -> None:
    r = api.get(f"{URL}/{id_finca}", headers=lider.headers)
    assert r.status_code == 404 and r.json()["codigo"] == "NO_ENCONTRADO"


# --- GET /fincas -------------------------------------------------------------------------------------------------


def nombres(api: TestClient, lider: Cuenta, **filtros: Any) -> list[str]:
    r = api.get(URL, params=filtros, headers=lider.headers)
    assert r.status_code == 200, r.text
    return [f["nombre"] for f in r.json()["items"]]


@pytest.fixture
def zona(api: TestClient, lider: Cuenta, catalogos: None, migrated: Engine) -> dict[str, Any]:
    """Tres fincas: Roble (San Luis, Álvaro, invernadero de maracuyá), Esperanza (Centro, Ana, campo de gulupa, con caso) y Alba (Centro, Ana, sin cultivos)."""
    ana = crear_productor(api, lider, 1, nombre="Ana Pérez")
    alvaro = crear_productor(api, lider, 2, nombre="Álvaro Díaz")
    esperanza = crear_finca(api, lider, ana["id"], "La Esperanza", "Centro")
    alba = crear_finca(api, lider, ana["id"], "El Alba", "Centro")
    roble = crear_finca(api, lider, alvaro["id"], "El Roble", "San Luis")
    c_esp = crear_cultivo(api, lider, esperanza["id"], "Lote 1", "CAMPO_ABIERTO", ("GULUPA",))["cultivos"][0]
    crear_cultivo(api, lider, roble["id"], "Lote 1", "INVERNADERO", ("MARACUYA",))
    crear_caso(migrated, c_esp["id"], "GULUPA", "En revisión")
    return {"ana": ana, "alvaro": alvaro, "esperanza": esperanza, "alba": alba, "roble": roble, "c_esp": c_esp}


def test_ordena_por_vereda_y_luego_por_nombre(api: TestClient, lider: Cuenta, zona: dict[str, Any]) -> None:
    assert nombres(api, lider) == ["El Alba", "La Esperanza", "El Roble"]


def test_sin_fincas_la_lista_viene_vacia(api: TestClient, lider: Cuenta) -> None:
    assert api.get(URL, headers=lider.headers).json() == {"items": [], "total": 0, "page": 1, "page_size": 20}


def test_busca_por_finca_o_productor_sin_tildes(api: TestClient, lider: Cuenta, zona: dict[str, Any]) -> None:
    assert nombres(api, lider, q="roble") == ["El Roble"]
    assert nombres(api, lider, q="alvaro") == ["El Roble"]
    assert nombres(api, lider, q="PEREZ") == ["El Alba", "La Esperanza"]
    assert nombres(api, lider, q="zzz") == []


def test_la_busqueda_no_es_inyectable(api: TestClient, lider: Cuenta, zona: dict[str, Any]) -> None:
    for q in ("'; DROP TABLE fincas; --", "%", "_", "\\"):
        assert api.get(URL, params={"q": q}, headers=lider.headers).status_code == 200
    assert len(nombres(api, lider)) == 3


def test_filtra_por_vereda(api: TestClient, lider: Cuenta, zona: dict[str, Any]) -> None:
    assert nombres(api, lider, vereda="SAN_LUIS") == ["El Roble"]
    assert nombres(api, lider, vereda="CENTRO") == ["El Alba", "La Esperanza"]
    assert nombres(api, lider, vereda="OTRA") == []


def test_filtra_por_especie_y_por_tipo_de_cultivo(api: TestClient, lider: Cuenta, zona: dict[str, Any]) -> None:
    assert nombres(api, lider, especie="GULUPA") == ["La Esperanza"]
    assert nombres(api, lider, especie="MARACUYA") == ["El Roble"]
    assert nombres(api, lider, tipo="INVERNADERO") == ["El Roble"]
    assert nombres(api, lider, tipo="CAMPO_ABIERTO") == ["La Esperanza"]


def test_filtra_por_casos_activos(api: TestClient, lider: Cuenta, zona: dict[str, Any]) -> None:
    assert nombres(api, lider, casos="con") == ["La Esperanza"]
    assert nombres(api, lider, casos="sin") == ["El Alba", "El Roble"]


def test_filtra_por_productor(api: TestClient, lider: Cuenta, zona: dict[str, Any]) -> None:
    assert nombres(api, lider, productor_id=zona["alvaro"]["id"]) == ["El Roble"]
    assert nombres(api, lider, productor_id="PRO-999999") == []


def test_combina_filtros(api: TestClient, lider: Cuenta, zona: dict[str, Any]) -> None:
    assert nombres(api, lider, vereda="CENTRO", especie="GULUPA", casos="con", tipo="CAMPO_ABIERTO") == ["La Esperanza"]
    assert nombres(api, lider, vereda="SAN_LUIS", especie="GULUPA") == []


def test_pagina_y_cuenta_el_total(api: TestClient, lider: Cuenta, catalogos: None) -> None:
    p = crear_productor(api, lider)
    for n in range(1, 6):
        crear_finca(api, lider, p["id"], f"Finca {n}")
    paginas = [api.get(URL, params={"page": n, "page_size": 2}, headers=lider.headers).json() for n in (1, 2, 3, 4)]
    assert [x["total"] for x in paginas] == [5, 5, 5, 5]
    assert [len(x["items"]) for x in paginas] == [2, 2, 1, 0]


@pytest.mark.parametrize(
    "parametros", [{"page": 0}, {"page_size": 101}, {"especie": "CURUBA"}, {"tipo": "SEMILLERO"}, {"casos": "algunos"}]
)
def test_filtros_invalidos_422(api: TestClient, lider: Cuenta, parametros: dict[str, object]) -> None:
    assert api.get(URL, params=parametros, headers=lider.headers).status_code == 422


# --- PATCH /fincas/{id} ------------------------------------------------------------------------------------------


@pytest.fixture
def una_finca(api: TestClient, lider: Cuenta, catalogos: None) -> dict[str, Any]:
    p = crear_productor(api, lider)
    finca = crear_finca(api, lider, p["id"])
    return crear_cultivo(api, lider, finca["id"], "Lote 1", "CAMPO_ABIERTO", ("GULUPA", "MARACUYA"))


def test_edita_los_datos_de_la_finca(
    api: TestClient, lider: Cuenta, una_finca: dict[str, Any], migrated: Engine
) -> None:
    r = api.patch(
        f"{URL}/{una_finca['id']}",
        json={"nombre": "Nueva Esperanza", "vereda": "San Luis", "latitud": 5.27, "longitud": -73.18},
        headers=lider.headers,
    )
    assert r.status_code == 200
    assert (r.json()["nombre"], r.json()["vereda"], r.json()["latitud"], r.json()["longitud"]) == (
        "Nueva Esperanza",
        "SAN_LUIS",
        5.27,
        -73.18,
    )


def test_al_mover_la_finca_sus_cultivos_la_siguen(
    api: TestClient, lider: Cuenta, una_finca: dict[str, Any], migrated: Engine
) -> None:
    api.patch(f"{URL}/{una_finca['id']}", json={"latitud": 5.27, "longitud": -73.18}, headers=lider.headers)
    cultivo = consultar(migrated, "SELECT latitud_cultivo AS la, longitud_cultivo AS lo FROM cultivos")[0]
    assert (float(cultivo["la"]), float(cultivo["lo"])) == (5.27, -73.18)


def test_edita_un_solo_campo_y_conserva_el_resto(api: TestClient, lider: Cuenta, una_finca: dict[str, Any]) -> None:
    r = api.patch(f"{URL}/{una_finca['id']}", json={"nombre": "Otro Nombre"}, headers=lider.headers).json()
    assert (r["nombre"], r["vereda"], r["latitud"]) == ("Otro Nombre", "CENTRO", 5.25)


def test_sin_cambios_no_audita(api: TestClient, lider: Cuenta, una_finca: dict[str, Any], migrated: Engine) -> None:
    antes = len(auditoria_de(migrated))
    r = api.patch(
        f"{URL}/{una_finca['id']}",
        json={"nombre": "La Esperanza", "latitud": 5.25, "vereda": "centro"},
        headers=lider.headers,
    )
    assert r.status_code == 200 and len(auditoria_de(migrated)) == antes


def test_audita_la_edicion(api: TestClient, lider: Cuenta, una_finca: dict[str, Any], migrated: Engine) -> None:
    api.patch(f"{URL}/{una_finca['id']}", json={"nombre": "Nueva"}, headers=lider.headers)
    registro = auditoria_de(migrated)[-1]
    assert registro["accion"] == "EDITAR_FINCA" and "La Esperanza" in registro["valor_anterior"]
    assert "Nueva" in registro["valor_nuevo"]


def test_las_coordenadas_siguen_validandose_al_editar(
    api: TestClient, lider: Cuenta, una_finca: dict[str, Any]
) -> None:
    r = api.patch(f"{URL}/{una_finca['id']}", json={"latitud": 120, "nombre": ""}, headers=lider.headers)
    assert r.status_code == 422 and set(r.json()["campos"]) == {"latitud", "nombre"}


def test_editar_una_finca_inexistente_404(api: TestClient, lider: Cuenta) -> None:
    assert api.patch(f"{URL}/FIN-999999", json={"nombre": "X"}, headers=lider.headers).status_code == 404


def test_edita_tipo_y_especies_de_sus_cultivos_en_el_mismo_patch(
    api: TestClient, lider: Cuenta, una_finca: dict[str, Any]
) -> None:
    id_cultivo = una_finca["cultivos"][0]["id"]
    r = api.patch(
        f"{URL}/{una_finca['id']}",
        json={"cultivos": [{"id": id_cultivo, "tipo": "INVERNADERO", "especies": ["GRANADILLA", "GULUPA"]}]},
        headers=lider.headers,
    )
    cultivo = r.json()["cultivos"][0]
    assert (cultivo["tipo"], cultivo["especies"]) == ("INVERNADERO", ["GRANADILLA", "GULUPA"])


def test_un_cultivo_ajeno_a_la_finca_se_rechaza(api: TestClient, lider: Cuenta, una_finca: dict[str, Any]) -> None:
    r = api.patch(
        f"{URL}/{una_finca['id']}",
        json={"cultivos": [{"id": "CUL-999999", "tipo": "INVERNADERO"}]},
        headers=lider.headers,
    )
    assert r.status_code == 422 and "cultivos.CUL-999999" in r.json()["campos"]


def test_quitar_una_especie_en_uso_409_con_los_casos_afectados(
    api: TestClient, lider: Cuenta, una_finca: dict[str, Any], migrated: Engine
) -> None:
    id_cultivo = una_finca["cultivos"][0]["id"]
    caso = crear_caso(migrated, id_cultivo, "MARACUYA", "En revisión")
    r = api.patch(
        f"{URL}/{una_finca['id']}",
        json={"cultivos": [{"id": id_cultivo, "especies": ["GULUPA"]}]},
        headers=lider.headers,
    )
    assert r.status_code == 409 and r.json()["codigo"] == "ESPECIE_EN_USO"
    assert r.json()["detalle"] == [{"cultivo": "Lote 1", "especie": "MARACUYA", "casos": [caso]}]
    assert api.get(f"{URL}/{una_finca['id']}", headers=lider.headers).json()["cultivos"][0]["especies"] == [
        "GULUPA",
        "MARACUYA",
    ]


def test_forzar_no_salta_la_proteccion(
    api: TestClient, lider: Cuenta, una_finca: dict[str, Any], migrated: Engine
) -> None:
    id_cultivo = una_finca["cultivos"][0]["id"]
    crear_caso(migrated, id_cultivo, "MARACUYA", "En revisión")
    r = api.patch(
        f"{URL}/{una_finca['id']}",
        json={"forzar": True, "cultivos": [{"id": id_cultivo, "especies": ["GULUPA"]}]},
        headers=lider.headers,
    )
    assert r.status_code == 409


def test_tampoco_se_quita_una_especie_de_un_caso_ya_cerrado(
    api: TestClient, lider: Cuenta, una_finca: dict[str, Any], migrated: Engine
) -> None:
    """El caso cerrado se conserva 6 meses y apunta al par (cultivo, especie): ese par no puede desaparecer."""
    id_cultivo = una_finca["cultivos"][0]["id"]
    crear_caso(migrated, id_cultivo, "MARACUYA", "Cerrado")
    r = api.patch(
        f"{URL}/{una_finca['id']}",
        json={"cultivos": [{"id": id_cultivo, "especies": ["GULUPA"]}]},
        headers=lider.headers,
    )
    assert r.status_code == 409 and r.json()["codigo"] == "ESPECIE_EN_USO"


def test_quitar_una_especie_sin_casos_es_posible(
    api: TestClient, lider: Cuenta, una_finca: dict[str, Any], migrated: Engine
) -> None:
    id_cultivo = una_finca["cultivos"][0]["id"]
    crear_caso(migrated, id_cultivo, "GULUPA", "En revisión")  # el caso usa gulupa; maracuyá queda libre
    r = api.patch(
        f"{URL}/{una_finca['id']}",
        json={"cultivos": [{"id": id_cultivo, "especies": ["GULUPA"]}]},
        headers=lider.headers,
    )
    assert r.status_code == 200 and r.json()["cultivos"][0]["especies"] == ["GULUPA"]


def test_un_cultivo_no_puede_quedarse_sin_especies(api: TestClient, lider: Cuenta, una_finca: dict[str, Any]) -> None:
    id_cultivo = una_finca["cultivos"][0]["id"]
    r = api.patch(
        f"{URL}/{una_finca['id']}", json={"cultivos": [{"id": id_cultivo, "especies": []}]}, headers=lider.headers
    )
    assert r.status_code == 422 and "cultivos.0.especies" in r.json()["campos"]


# --- cultivos ----------------------------------------------------------------------------------------------------


def test_agrega_un_cultivo_y_responde_con_la_finca(
    api: TestClient, lider: Cuenta, catalogos: None, migrated: Engine
) -> None:
    p = crear_productor(api, lider)
    finca = crear_finca(api, lider, p["id"], latitud=5.26, longitud=-73.16)
    r = api.post(
        f"{URL}/{finca['id']}/cultivos",
        json={"nombre": "Lote Norte", "tipo": "INVERNADERO", "especies": ["GULUPA", "GRANADILLA"]},
        headers=lider.headers,
    )
    assert r.status_code == 201 and r.json()["id"] == finca["id"]
    [cultivo] = r.json()["cultivos"]
    assert cultivo["id"].startswith("CUL-") and (cultivo["nombre"], cultivo["tipo"]) == ("Lote Norte", "INVERNADERO")
    assert cultivo["especies"] == ["GRANADILLA", "GULUPA"] and cultivo["casos_activos"] == []
    fila = consultar(migrated, "SELECT * FROM cultivos")[0]
    assert (float(fila["latitud_cultivo"]), float(fila["longitud_cultivo"])) == (5.26, -73.16)  # hereda la ubicación
    assert fila["tipo_cultivo"] == "INVERNADERO" and fila["ubicacion"] is not None


def test_un_finca_admite_varios_cultivos(api: TestClient, lider: Cuenta, catalogos: None) -> None:
    p = crear_productor(api, lider)
    finca = crear_finca(api, lider, p["id"])
    crear_cultivo(api, lider, finca["id"], "Lote 1")
    r = crear_cultivo(api, lider, finca["id"], "Lote 2", "INVERNADERO", ("MARACUYA",))
    assert [c["nombre"] for c in r["cultivos"]] == ["Lote 1", "Lote 2"]


def test_las_especies_repetidas_se_unifican(api: TestClient, lider: Cuenta, catalogos: None) -> None:
    p = crear_productor(api, lider)
    finca = crear_finca(api, lider, p["id"])
    r = crear_cultivo(api, lider, finca["id"], especies=("GULUPA", "GULUPA"))
    assert r["cultivos"][0]["especies"] == ["GULUPA"]


def test_el_nombre_del_cultivo_no_se_repite_en_la_finca(api: TestClient, lider: Cuenta, catalogos: None) -> None:
    p = crear_productor(api, lider)
    finca = crear_finca(api, lider, p["id"])
    crear_cultivo(api, lider, finca["id"], "Lote Único")
    r = api.post(
        f"{URL}/{finca['id']}/cultivos",
        json={"nombre": "lote unico", "tipo": "INVERNADERO", "especies": ["GULUPA"]},
        headers=lider.headers,
    )
    assert r.status_code == 409 and r.json()["campos"] == {"nombre": "Esta finca ya tiene un cultivo con ese nombre"}


def test_el_mismo_nombre_en_otra_finca_si_se_permite(api: TestClient, lider: Cuenta, catalogos: None) -> None:
    p = crear_productor(api, lider)
    una, otra = crear_finca(api, lider, p["id"], "Una"), crear_finca(api, lider, p["id"], "Otra")
    crear_cultivo(api, lider, una["id"], "Lote 1")
    assert crear_cultivo(api, lider, otra["id"], "Lote 1")["cultivos"][0]["nombre"] == "Lote 1"


@pytest.mark.parametrize(
    "cambio",
    [
        {"nombre": ""},
        {"tipo": "SEMILLERO"},
        {"tipo": None},
        {"especies": []},
        {"especies": ["CURUBA"]},
        {"especies": None},
    ],
)
def test_cultivo_invalido_422(api: TestClient, lider: Cuenta, catalogos: None, cambio: dict[str, Any]) -> None:
    p = crear_productor(api, lider)
    finca = crear_finca(api, lider, p["id"])
    datos = {"nombre": "Lote", "tipo": "INVERNADERO", "especies": ["GULUPA"], **cambio}
    r = api.post(f"{URL}/{finca['id']}/cultivos", json=datos, headers=lider.headers)
    assert r.status_code == 422 and r.json()["codigo"] == "VALIDACION"


def test_cultivo_en_una_finca_inexistente_404(api: TestClient, lider: Cuenta, catalogos: None) -> None:
    datos = {"nombre": "Lote", "tipo": "INVERNADERO", "especies": ["GULUPA"]}
    assert api.post(f"{URL}/FIN-999999/cultivos", json=datos, headers=lider.headers).status_code == 404


def test_audita_el_alta_del_cultivo(api: TestClient, lider: Cuenta, catalogos: None, migrated: Engine) -> None:
    p = crear_productor(api, lider)
    finca = crear_finca(api, lider, p["id"])
    id_cultivo = crear_cultivo(api, lider, finca["id"])["cultivos"][0]["id"]
    registro = auditoria_de(migrated)[-1]
    assert (registro["accion"], registro["entidad_afectada"]) == ("CREAR_CULTIVO", "cultivos")
    assert id_cultivo in registro["descripcion"] and finca["id"] in registro["descripcion"]


def test_lista_los_cultivos_de_una_finca(api: TestClient, lider: Cuenta, una_finca: dict[str, Any]) -> None:
    r = api.get(f"{URL}/{una_finca['id']}/cultivos", headers=lider.headers)
    assert r.status_code == 200 and [c["nombre"] for c in r.json()] == ["Lote 1"]
    assert api.get(f"{URL}/FIN-999999/cultivos", headers=lider.headers).status_code == 404


def test_dos_altas_simultaneas_del_mismo_cultivo_dejan_uno(
    api: TestClient, lider: Cuenta, catalogos: None, migrated: Engine
) -> None:
    p = crear_productor(api, lider)
    finca = crear_finca(api, lider, p["id"])
    datos = {"nombre": "Lote Carrera", "tipo": "INVERNADERO", "especies": ["GULUPA"]}

    async def disparar() -> list[httpx.Response]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as cliente:
            return await asyncio.gather(
                *(cliente.post(f"{URL}/{finca['id']}/cultivos", json=datos, headers=lider.headers) for _ in range(3))
            )

    assert sorted(r.status_code for r in asyncio.run(disparar())) == [201, 409, 409]
    assert len(consultar(migrated, "SELECT 1 FROM cultivos")) == 1


# --- PATCH /cultivos/{id} ----------------------------------------------------------------------------------------


def test_edita_un_cultivo(api: TestClient, lider: Cuenta, una_finca: dict[str, Any], migrated: Engine) -> None:
    id_cultivo = una_finca["cultivos"][0]["id"]
    r = api.patch(
        f"{CULTIVOS}/{id_cultivo}",
        json={"nombre": "Lote Renovado", "tipo": "INVERNADERO", "especies": ["GRANADILLA"]},
        headers=lider.headers,
    )
    assert r.status_code == 200
    assert (r.json()["nombre"], r.json()["tipo"], r.json()["especies"]) == (
        "Lote Renovado",
        "INVERNADERO",
        ["GRANADILLA"],
    )
    assert consultar(migrated, "SELECT count(*) AS n FROM cultivo_especie")[0]["n"] == 1


def test_editar_un_cultivo_sin_cambios_no_audita(
    api: TestClient, lider: Cuenta, una_finca: dict[str, Any], migrated: Engine
) -> None:
    antes = len(auditoria_de(migrated))
    id_cultivo = una_finca["cultivos"][0]["id"]
    r = api.patch(
        f"{CULTIVOS}/{id_cultivo}", json={"nombre": "Lote 1", "especies": ["MARACUYA", "GULUPA"]}, headers=lider.headers
    )
    assert r.status_code == 200 and len(auditoria_de(migrated)) == antes


def test_renombrar_a_un_nombre_ya_usado_409(api: TestClient, lider: Cuenta, una_finca: dict[str, Any]) -> None:
    crear_cultivo(api, lider, una_finca["id"], "Lote 2")
    otro = api.get(f"{URL}/{una_finca['id']}/cultivos", headers=lider.headers).json()[1]["id"]
    r = api.patch(f"{CULTIVOS}/{otro}", json={"nombre": "LOTE 1"}, headers=lider.headers)
    assert r.status_code == 409 and "nombre" in r.json()["campos"]


def test_conservar_su_propio_nombre_no_es_duplicado(api: TestClient, lider: Cuenta, una_finca: dict[str, Any]) -> None:
    id_cultivo = una_finca["cultivos"][0]["id"]
    assert (
        api.patch(
            f"{CULTIVOS}/{id_cultivo}", json={"nombre": "lote 1", "tipo": "INVERNADERO"}, headers=lider.headers
        ).status_code
        == 200
    )


def test_quitar_una_especie_en_uso_desde_el_cultivo_409(
    api: TestClient, lider: Cuenta, una_finca: dict[str, Any], migrated: Engine
) -> None:
    id_cultivo = una_finca["cultivos"][0]["id"]
    casos = sorted(
        [
            crear_caso(migrated, id_cultivo, "GULUPA", "En revisión"),
            crear_caso(migrated, id_cultivo, "GULUPA", "Registrado"),
        ]
    )
    r = api.patch(f"{CULTIVOS}/{id_cultivo}", json={"especies": ["MARACUYA"]}, headers=lider.headers)
    assert r.status_code == 409 and r.json()["codigo"] == "ESPECIE_EN_USO"
    assert r.json()["detalle"] == [{"cultivo": "Lote 1", "especie": "GULUPA", "casos": casos}]
    assert auditoria_de(migrated)[-1]["accion"] != "EDITAR_CULTIVO"


def test_el_cultivo_no_puede_quedarse_sin_especies(api: TestClient, lider: Cuenta, una_finca: dict[str, Any]) -> None:
    id_cultivo = una_finca["cultivos"][0]["id"]
    assert api.patch(f"{CULTIVOS}/{id_cultivo}", json={"especies": []}, headers=lider.headers).status_code == 422


def test_editar_un_cultivo_inexistente_404(api: TestClient, lider: Cuenta) -> None:
    assert api.patch(f"{CULTIVOS}/CUL-999999", json={"nombre": "X"}, headers=lider.headers).status_code == 404


def test_audita_la_edicion_del_cultivo(
    api: TestClient, lider: Cuenta, una_finca: dict[str, Any], migrated: Engine
) -> None:
    id_cultivo = una_finca["cultivos"][0]["id"]
    api.patch(
        f"{CULTIVOS}/{id_cultivo}",
        json={"tipo": "INVERNADERO", "especies": ["GULUPA", "GRANADILLA"]},
        headers=lider.headers,
    )
    registro = auditoria_de(migrated)[-1]
    assert (registro["accion"], registro["entidad_afectada"]) == ("EDITAR_CULTIVO", "cultivos")
    assert "GRANADILLA" in registro["valor_nuevo"] and "MARACUYA" in registro["valor_nuevo"]


# --- catálogos ---------------------------------------------------------------------------------------------------


def test_catalogo_de_especies(api: TestClient, lider: Cuenta, catalogos: None) -> None:
    r = api.get("/api/v1/especies", headers=lider.headers)
    assert r.status_code == 200
    assert r.json() == [
        {"codigo": "GULUPA", "nombre": "Gulupa"},
        {"codigo": "GRANADILLA", "nombre": "Granadilla"},
        {"codigo": "MARACUYA", "nombre": "Maracuyá"},
    ]


def test_catalogo_de_categorias_marca_las_transmisibles(api: TestClient, lider: Cuenta, catalogos: None) -> None:
    r = api.get("/api/v1/categorias", headers=lider.headers).json()
    assert {c["codigo"] for c in r} == {"BOTRYTIS", "RONA", "DEFICIENCIA_MAGNESIO", "TRIPS", "FUSARIUM"}
    assert {c["codigo"] for c in r if c["es_transmisible"]} == {"TRIPS"}


def test_las_veredas_son_las_de_las_fincas_registradas(api: TestClient, lider: Cuenta, catalogos: None) -> None:
    assert api.get("/api/v1/veredas", headers=lider.headers).json() == []
    p = crear_productor(api, lider)
    crear_finca(api, lider, p["id"], "Una", "San Luis")
    crear_finca(api, lider, p["id"], "Otra", "SAN_LUIS")
    crear_finca(api, lider, p["id"], "Tercera", "Centro")
    assert api.get("/api/v1/veredas", headers=lider.headers).json() == [
        {"codigo": "CENTRO", "nombre": "Centro"},
        {"codigo": "SAN_LUIS", "nombre": "San Luis"},
    ]


@pytest.mark.parametrize("ruta", ["/api/v1/especies", "/api/v1/categorias", "/api/v1/veredas"])
def test_el_tecnico_tambien_consulta_los_catalogos(
    api: TestClient, nueva_cuenta: NuevaCuenta, catalogos: None, ruta: str
) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    assert api.get(ruta, headers=ana.headers).status_code == 200
    assert api.get(ruta).status_code == 401
