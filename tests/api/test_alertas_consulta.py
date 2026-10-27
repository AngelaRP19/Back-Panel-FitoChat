"""GET /alertas, /alertas/{id}, parámetros epidemiológicos y «seguir»: lo que consultan líder y técnico."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from tests.api.conftest import Mundo, auditoria_de, consultar
from tests.api.conftest_alertas import Zona, construir_zona
from tests.api.territorio import crear_alerta, crear_caso, crear_identificacion

URL = "/api/v1/alertas"


@pytest.fixture
def zona(api: TestClient, mundo: Mundo, migrated: Engine) -> Zona:
    return construir_zona(api, mundo, migrated)


def pedir(api: TestClient, actor: Any, ruta: str = URL, **filtros: Any) -> Any:
    r = api.get(ruta, params=filtros, headers=actor.headers)
    assert r.status_code == 200, r.text
    return r.json()


# --- permisos ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("ruta", [URL, f"{URL}/ALR-000001", f"{URL}/parametros"])
def test_sin_sesion_401(api: TestClient, ruta: str) -> None:
    assert api.get(ruta).status_code == 401


def test_el_tecnico_consulta_pero_no_decide(api: TestClient, mundo: Mundo, zona: Zona) -> None:
    """Criterio de aceptación: el técnico no puede confirmar, descartar ni comunicar (403)."""
    for ruta in (URL, f"{URL}/{zona.alerta}", f"{URL}/parametros"):
        assert api.get(ruta, headers=mundo.t1.headers).status_code == 200
    intentos = [
        api.post(f"{URL}/{zona.alerta}/confirmar", headers=mundo.t1.headers),
        api.post(f"{URL}/{zona.alerta}/descartar", json={"justificacion": "x"}, headers=mundo.t1.headers),
        api.get(f"{URL}/{zona.alerta}/destinatarios", headers=mundo.t1.headers),
        api.put(f"{URL}/{zona.alerta}/borrador", json={"mensaje": "Revise sus plantas"}, headers=mundo.t1.headers),
        api.post(
            f"{URL}/{zona.alerta}/comunicar", json={"mensaje": "x", "veredas": ["CENTRO"]}, headers=mundo.t1.headers
        ),
        api.post(f"{URL}/{zona.alerta}/corregir", json={"mensaje": "x"}, headers=mundo.t1.headers),
        api.put(
            f"{URL}/parametros/TRIPS",
            json={"num_casos": 3, "distancia_km": 3.5, "intervalo_dias": 30},
            headers=mundo.t1.headers,
        ),
    ]
    assert [r.status_code for r in intentos] == [403] * 7
    assert {r.json()["codigo"] for r in intentos} == {"SIN_PERMISO"}


def test_las_acciones_del_lider_sin_sesion_401(api: TestClient, zona: Zona) -> None:
    assert api.post(f"{URL}/{zona.alerta}/confirmar").status_code == 401
    assert api.put(f"{URL}/parametros/TRIPS", json={}).status_code == 401


# --- listado -----------------------------------------------------------------------------------------------------


def test_lista_vacia(api: TestClient, mundo: Mundo) -> None:
    r = pedir(api, mundo.lider)
    assert r["items"] == [] and r["total"] == 0
    assert r["conteos"] == {
        "TODAS": 0, "GENERADA": 0, "EN_REVISION": 0, "CONFIRMADA": 0, "DESCARTADA": 0, "COMUNICADA": 0, "CORREGIDA": 0,
    }  # fmt: skip


def test_cada_alerta_trae_los_campos_del_contrato(api: TestClient, mundo: Mundo, zona: Zona) -> None:
    [a] = pedir(api, mundo.lider)["items"]
    assert set(a) == {
        "id", "codigo", "estado", "categoria", "vereda", "generada_en", "num_casos", "radio_km", "parametros",
        "revisa", "confirmada_por", "productores_notificados", "envios_fallidos", "motivo_descarte",
    }  # fmt: skip
    assert a["id"] == zona.alerta and a["codigo"] == f"ALR-{int(zona.alerta[4:]):03d}"
    assert (a["estado"], a["categoria"], a["num_casos"]) == ("GENERADA", "TRIPS", 3)
    assert a["vereda"] == "CENTRO"  # la vereda donde más casos se concentran: dos en La Esperanza y uno en La Cercana
    assert a["parametros"] == {"num_casos": 3, "distancia_km": 3.5, "intervalo_dias": 30}
    assert (a["revisa"], a["confirmada_por"], a["productores_notificados"], a["motivo_descarte"]) == (
        None,
        None,
        None,
        None,
    )


def test_el_radio_es_la_distancia_maxima_real_entre_los_casos(api: TestClient, mundo: Mundo, zona: Zona) -> None:
    [a] = pedir(api, mundo.lider)["items"]
    assert 2.15 <= a["radio_km"] <= 2.3  # 0,02° de latitud ≈ 2,2 km


def test_una_alerta_conserva_los_parametros_con_que_nacio(
    api: TestClient, mundo: Mundo, migrated: Engine, zona: Zona
) -> None:
    api.put(
        f"{URL}/parametros/TRIPS",
        json={"num_casos": 5, "distancia_km": 9, "intervalo_dias": 60},
        headers=mundo.lider.headers,
    )
    assert pedir(api, mundo.lider)["items"][0]["parametros"] == {
        "num_casos": 3,
        "distancia_km": 3.5,
        "intervalo_dias": 30,
    }


def test_orden_conteos_y_filtro_por_estado(api: TestClient, mundo: Mundo, migrated: Engine, zona: Zona) -> None:
    confirmada = crear_alerta(migrated, zona.casos[:2], "CONFIRMADA")
    descartada = crear_alerta(migrated, zona.casos[:1], "DESCARTADA", justificacion_descarte="Falsa alarma")
    r = pedir(api, mundo.lider)
    assert [a["id"] for a in r["items"]] == [descartada, confirmada, zona.alerta]  # las más recientes primero
    assert r["conteos"]["TODAS"] == 3 and r["conteos"]["GENERADA"] == 1 and r["conteos"]["CONFIRMADA"] == 1
    assert r["conteos"]["DESCARTADA"] == 1 and r["conteos"]["COMUNICADA"] == 0
    solo = pedir(api, mundo.lider, estado="CONFIRMADA")
    assert [a["id"] for a in solo["items"]] == [confirmada] and solo["total"] == 1
    assert solo["conteos"]["TODAS"] == 3  # las pestañas no cambian con el filtro
    assert pedir(api, mundo.lider, estado="CORREGIDA")["items"] == []


def test_el_motivo_del_descarte_sale_en_el_listado(api: TestClient, mundo: Mundo, migrated: Engine, zona: Zona) -> None:
    crear_alerta(migrated, zona.casos, "DESCARTADA", justificacion_descarte="Falsa alarma")
    [a] = pedir(api, mundo.lider, estado="DESCARTADA")["items"]
    assert a["motivo_descarte"] == "Falsa alarma" and a["confirmada_por"] is None


def test_pagina(api: TestClient, mundo: Mundo, migrated: Engine, zona: Zona) -> None:
    for _ in range(4):
        crear_alerta(migrated, zona.casos)
    paginas = [pedir(api, mundo.lider, page=p, page_size=2) for p in (1, 2, 3)]
    assert [x["total"] for x in paginas] == [5, 5, 5] and [len(x["items"]) for x in paginas] == [2, 2, 1]
    assert pedir(api, mundo.lider, page=9)["items"] == []


@pytest.mark.parametrize("parametros", [{"estado": "ABIERTA"}, {"page": 0}, {"page_size": 101}])
def test_filtros_invalidos_422(api: TestClient, mundo: Mundo, parametros: dict[str, object]) -> None:
    assert api.get(URL, params=parametros, headers=mundo.lider.headers).status_code == 422


def test_el_tecnico_ve_las_mismas_alertas(api: TestClient, mundo: Mundo, zona: Zona) -> None:
    assert [a["id"] for a in pedir(api, mundo.t2)["items"]] == [zona.alerta]


# --- detalle -----------------------------------------------------------------------------------------------------


def test_el_detalle_trae_los_casos_que_la_originan(api: TestClient, mundo: Mundo, zona: Zona) -> None:
    d = pedir(api, mundo.lider, f"{URL}/{zona.alerta}")
    assert d["id"] == zona.alerta and len(d["casos"]) == 3
    primero = d["casos"][0]
    assert set(primero) == {
        "id", "codigo", "productor_nombre", "finca_nombre", "resultado_tecnico", "enviado_en", "latitud", "longitud",
        "enlazable",
    }  # fmt: skip
    assert primero["id"] == zona.casos[0] and primero["codigo"].startswith("#")
    assert (primero["productor_nombre"], primero["finca_nombre"]) == ("Ana Pérez", "La Esperanza")
    assert (primero["latitud"], primero["longitud"]) == (5.25, -73.17)
    assert all(c["enlazable"] for c in d["casos"]) and all(c["resultado_tecnico"] == "CONFIRMADO" for c in d["casos"])
    assert d["borrador"] is None and d["descarte"] is None and d["correccion"] is None and d["mensaje_enviado"] is None
    assert d["siguiendo"] is False


def test_el_tecnico_solo_puede_abrir_sus_casos_y_no_ve_los_productores_ajenos(
    api: TestClient, mundo: Mundo, zona: Zona
) -> None:
    d = pedir(api, mundo.t1, f"{URL}/{zona.alerta}")
    por_id = {c["id"]: c for c in d["casos"]}
    suyos = [c for c in d["casos"] if c["enlazable"]]
    assert {c["id"] for c in suyos} == set(zona.casos[:2])  # los dos asignados a la técnica 1
    assert por_id[zona.casos[0]]["productor_nombre"] == "Ana Pérez"
    ajeno = por_id[zona.casos[2]]
    assert ajeno["enlazable"] is False and ajeno["productor_nombre"] is None
    assert "Carlos Cerca" not in str(d)


@pytest.mark.parametrize("id_alerta", ["ALR-999999", "CAS-00000001", "x"])
def test_alerta_inexistente_404(api: TestClient, mundo: Mundo, id_alerta: str) -> None:
    r = api.get(f"{URL}/{id_alerta}", headers=mundo.lider.headers)
    assert r.status_code == 404 and r.json()["codigo"] == "NO_ENCONTRADO"


def test_el_detalle_no_expone_telefonos(api: TestClient, mundo: Mundo, zona: Zona) -> None:
    cuerpo = api.get(f"{URL}/{zona.alerta}", headers=mundo.lider.headers).text
    assert "573001112" not in cuerpo and "3001112001" not in cuerpo


# --- parámetros epidemiológicos ----------------------------------------------------------------------------------

PARAMETROS = {"num_casos": 4, "distancia_km": 2.5, "intervalo_dias": 45}


def test_los_parametros_de_todas_las_categorias(api: TestClient, mundo: Mundo, catalogos: None) -> None:
    r = pedir(api, mundo.lider, f"{URL}/parametros")
    assert [p["categoria"] for p in r] == ["BOTRYTIS", "RONA", "DEFICIENCIA_MAGNESIO", "TRIPS", "FUSARIUM"]
    trips = next(p for p in r if p["categoria"] == "TRIPS")
    assert trips == {
        "categoria": "TRIPS", "nombre": "Trips", "habilitada": True, "num_casos": 3, "distancia_km": 3.5,
        "intervalo_dias": 30, "nota": None, "actualizado_por": None, "actualizado_en": trips["actualizado_en"],
    }  # fmt: skip
    for otra in (p for p in r if p["categoria"] != "TRIPS"):
        assert otra["habilitada"] is False and otra["num_casos"] is None and otra["nota"]


def test_el_lider_edita_los_parametros_y_queda_auditado(
    api: TestClient, mundo: Mundo, migrated: Engine, catalogos: None
) -> None:
    r = api.put(f"{URL}/parametros/TRIPS", json=PARAMETROS, headers=mundo.lider.headers)
    assert r.status_code == 200
    assert (r.json()["num_casos"], r.json()["distancia_km"], r.json()["intervalo_dias"]) == (4, 2.5, 45)
    assert r.json()["actualizado_por"] == {"id": mundo.lider.id, "nombre": "Laura Líder"} and r.json()["actualizado_en"]
    assert pedir(api, mundo.t1, f"{URL}/parametros")[3]["actualizado_por"]["nombre"] == "Laura Líder"
    fila = consultar(migrated, "SELECT * FROM parametros_epidemiologicos")[0]
    assert (fila["numero_minimo_casos"], float(fila["distancia_km"]), fila["intervalo_dias"]) == (4, 2.5, 45)
    [registro] = [a for a in auditoria_de(migrated) if a["accion"] == "EDITAR_PARAMETROS"]
    assert registro["id_lider_tecnico"] == mundo.lider.id and "3.5" in registro["valor_anterior"]
    assert '"distancia_km": 2.5' in registro["valor_nuevo"]


def test_se_edita_varias_veces_y_cuenta_la_ultima(
    api: TestClient, mundo: Mundo, catalogos: None, nueva_cuenta: Any
) -> None:
    from app.core.roles import Rol

    otro = nueva_cuenta(Rol.LIDER_TECNICO, 2, nombre="Otro Líder")
    api.put(f"{URL}/parametros/TRIPS", json=PARAMETROS, headers=mundo.lider.headers)
    r = api.put(f"{URL}/parametros/TRIPS", json={**PARAMETROS, "num_casos": 6}, headers=otro.headers)
    assert r.json()["num_casos"] == 6 and r.json()["actualizado_por"]["nombre"] == "Otro Líder"


@pytest.mark.parametrize("categoria", ["BOTRYTIS", "RONA", "DEFICIENCIA_MAGNESIO", "FUSARIUM"])
def test_solo_las_categorias_transmisibles_tienen_parametros(
    api: TestClient, mundo: Mundo, migrated: Engine, catalogos: None, categoria: str
) -> None:
    r = api.put(f"{URL}/parametros/{categoria}", json=PARAMETROS, headers=mundo.lider.headers)
    assert r.status_code == 422 and "categoria" in r.json()["campos"]
    assert len(consultar(migrated, "SELECT 1 FROM parametros_epidemiologicos")) == 1  # solo Trips


@pytest.mark.parametrize(
    "cambio",
    [
        {"num_casos": 1},
        {"num_casos": 1000},
        {"distancia_km": 0},
        {"distancia_km": 50.5},
        {"intervalo_dias": 0},
        {"intervalo_dias": 366},
        {"num_casos": "tres"},
    ],
)
def test_parametros_invalidos_422(api: TestClient, mundo: Mundo, catalogos: None, cambio: dict[str, Any]) -> None:
    r = api.put(f"{URL}/parametros/TRIPS", json={**PARAMETROS, **cambio}, headers=mundo.lider.headers)
    assert r.status_code == 422 and r.json()["codigo"] == "VALIDACION"


def test_una_categoria_desconocida_422(api: TestClient, mundo: Mundo, catalogos: None) -> None:
    assert api.put(f"{URL}/parametros/CURUBA", json=PARAMETROS, headers=mundo.lider.headers).status_code == 422


def test_cambiar_los_parametros_cambia_lo_que_evalua_sp_evaluar_alerta(
    api: TestClient, mundo: Mundo, migrated: Engine, catalogos: None
) -> None:
    """Con el mínimo en 3, dos casos confirmados no alertan; con el mínimo en 2, el segundo ya genera la alerta."""
    api.put(
        f"{URL}/parametros/TRIPS",
        json={"num_casos": 2, "distancia_km": 3.5, "intervalo_dias": 30},
        headers=mundo.lider.headers,
    )
    ids = []
    for _ in range(2):
        id_caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "En revisión", tecnico=mundo.t1.id)
        crear_identificacion(migrated, id_caso, "PRELIMINAR", {"TRIPS": 80})
        ids.append(id_caso)
        assert (
            api.post(
                f"/api/v1/casos/{id_caso}/diagnostico",
                json={"resultado_tecnico": "CONFIRMADO"},
                headers=mundo.t1.headers,
            ).status_code
            == 200
        )
    [alerta] = pedir(api, mundo.lider)["items"]
    assert alerta["num_casos"] == 2 and alerta["parametros"]["num_casos"] == 2


def test_con_el_minimo_original_dos_casos_no_generan_alerta(
    api: TestClient, mundo: Mundo, migrated: Engine, catalogos: None
) -> None:
    for _ in range(2):
        id_caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "En revisión", tecnico=mundo.t1.id)
        crear_identificacion(migrated, id_caso, "PRELIMINAR", {"TRIPS": 80})
        api.post(
            f"/api/v1/casos/{id_caso}/diagnostico", json={"resultado_tecnico": "CONFIRMADO"}, headers=mundo.t1.headers
        )
    assert pedir(api, mundo.lider)["items"] == []


# --- seguir ------------------------------------------------------------------------------------------------------


def test_el_tecnico_sigue_y_deja_de_seguir(api: TestClient, mundo: Mundo, migrated: Engine, zona: Zona) -> None:
    ruta = f"{URL}/{zona.alerta}/seguir"
    assert api.post(ruta, headers=mundo.t1.headers).json() == {"siguiendo": True}
    assert pedir(api, mundo.t1, f"{URL}/{zona.alerta}")["siguiendo"] is True
    assert pedir(api, mundo.t2, f"{URL}/{zona.alerta}")["siguiendo"] is False  # cada técnico, lo suyo
    assert api.delete(ruta, headers=mundo.t1.headers).json() == {"siguiendo": False}
    assert pedir(api, mundo.t1, f"{URL}/{zona.alerta}")["siguiendo"] is False


def test_seguir_es_idempotente(api: TestClient, mundo: Mundo, migrated: Engine, zona: Zona) -> None:
    for _ in range(3):
        api.post(f"{URL}/{zona.alerta}/seguir", headers=mundo.t1.headers)
    acciones = [a["accion"] for a in auditoria_de(migrated) if a["accion"] in ("SEGUIR_ALERTA", "DEJAR_SEGUIR")]
    assert acciones == ["SEGUIR_ALERTA"]


def test_solo_el_tecnico_sigue_alertas(api: TestClient, mundo: Mundo, zona: Zona) -> None:
    assert api.post(f"{URL}/{zona.alerta}/seguir", headers=mundo.lider.headers).status_code == 403
    assert api.post(f"{URL}/ALR-999999/seguir", headers=mundo.t1.headers).status_code == 404
