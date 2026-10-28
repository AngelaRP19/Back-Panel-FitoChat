"""B-02 y B-10: rutas de Inicio que usa el panel (/panel/*) y la campana (/notificaciones), con sus avisos."""

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.infrastructure.notificaciones_store import _EN_MEMORIA, Notificacion
from tests.api.conftest import Cuenta, Mundo
from tests.api.conftest_alertas import construir_zona
from tests.api.territorio import crear_caso, crear_identificacion

NOTIF = "/api/v1/notificaciones"
NAV = "/api/v1/resumen/navegacion"


def pedir(api: TestClient, actor: Cuenta, ruta: str, **params: Any) -> Any:
    r = api.get(ruta, params=params, headers=actor.headers)
    assert r.status_code == 200, r.text
    return r.json()


def avisos(api: TestClient, actor: Cuenta) -> list[dict[str, Any]]:
    return list(pedir(api, actor, NOTIF)["items"])


# --- /panel/* (B-02) -----------------------------------------------------------------------------------------------


def test_resumen_del_lider_con_la_forma_del_panel(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "Pendiente de revisión", enviado_hace=timedelta(hours=1))
    r = pedir(api, mundo.lider, "/api/v1/panel/resumen")
    assert {"generado_en", "kpis", "pendientes_antiguos", "pendientes_sin_tecnico", "alertas", "carga_tecnicos"} <= set(
        r
    )
    [pendiente] = r["pendientes_antiguos"]
    assert pendiente["id"] == caso and pendiente["numero"] == int(caso.rsplit("-", 1)[-1])


def test_resumen_del_tecnico_con_la_forma_del_panel(api: TestClient, mundo: Mundo) -> None:
    r = pedir(api, mundo.t1, "/api/v1/panel/resumen-tecnico")
    assert {"generado_en", "nombre", "lider_nombre", "kpis", "por_atender", "diagnosticos_recientes"} <= set(r)
    assert r["nombre"] == "Ana Técnica" and r["lider_nombre"] == "Laura Líder"


def test_casos_por_categoria_en_la_ruta_del_panel(api: TestClient, mundo: Mundo) -> None:
    r = pedir(api, mundo.lider, "/api/v1/panel/casos-por-categoria", periodo="30d")
    assert r["periodo"] == "30d" and r["total"] == 0


@pytest.mark.parametrize(
    ("ruta", "quien"),
    [
        ("/api/v1/panel/resumen", "t1"),
        ("/api/v1/panel/casos-por-categoria", "t1"),
        ("/api/v1/panel/resumen-tecnico", "lider"),
    ],
)
def test_cada_inicio_es_de_su_rol(api: TestClient, mundo: Mundo, ruta: str, quien: str) -> None:
    assert api.get(ruta, headers=getattr(mundo, quien).headers).status_code == 403


@pytest.mark.parametrize("ruta", ["/api/v1/panel/resumen", "/api/v1/panel/resumen-tecnico", NOTIF])
def test_sin_sesion_401(api: TestClient, ruta: str) -> None:
    assert api.get(ruta).status_code == 401


# --- /notificaciones (B-10) ----------------------------------------------------------------------------------------


def test_sin_avisos_la_campana_esta_vacia(api: TestClient, mundo: Mundo) -> None:
    assert pedir(api, mundo.lider, NOTIF) == {"items": [], "nuevas": 0}


def test_asignar_avisa_al_tecnico_y_suma_en_la_navegacion(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "Pendiente de revisión")
    r = api.patch(f"/api/v1/casos/{caso}/asignacion", json={"tecnico_id": mundo.t1.id}, headers=mundo.lider.headers)
    assert r.status_code == 200
    [aviso] = avisos(api, mundo.t1)
    assert aviso["tipo"] == "CASO_ASIGNADO" and aviso["titulo"] == "Caso asignado"
    assert aviso["enlace"] == f"/casos/{caso}" and "Ana Pérez" in aviso["texto"] and aviso["leida"] is False
    assert aviso["id"].startswith("NOT-") and aviso["en"]
    assert pedir(api, mundo.t1, NAV)["notificaciones_nuevas"] == 1
    assert avisos(api, mundo.t2) == [] and avisos(api, mundo.lider) == []  # solo al técnico asignado


def test_marcar_leidas_deja_el_contador_en_cero(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "Pendiente de revisión")
    api.patch(f"/api/v1/casos/{caso}/asignacion", json={"tecnico_id": mundo.t1.id}, headers=mundo.lider.headers)
    assert api.post(f"{NOTIF}/leidas", headers=mundo.t1.headers).status_code == 204
    lista = pedir(api, mundo.t1, NOTIF)
    assert lista["nuevas"] == 0 and all(n["leida"] for n in lista["items"])
    assert pedir(api, mundo.t1, NAV)["notificaciones_nuevas"] == 0


def test_el_diagnostico_avisa_a_los_lideres(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "En revisión", tecnico=mundo.t1.id)
    crear_identificacion(migrated, caso, "PRELIMINAR", {"BOTRYTIS": 80})
    r = api.post(
        f"/api/v1/casos/{caso}/diagnostico",
        json={"resultado_tecnico": "NO_IDENTIFICADO", "descripcion": "", "observaciones": ""},
        headers=mundo.t1.headers,
    )
    assert r.status_code == 200, r.text
    [aviso] = avisos(api, mundo.lider)
    assert aviso["tipo"] == "DIAGNOSTICO_REGISTRADO" and "No identificado" in aviso["texto"]
    assert "Ana Técnica" in aviso["texto"] and aviso["enlace"] == f"/casos/{caso}"


def test_confirmar_una_alerta_avisa_a_lideres_y_a_quien_la_sigue(
    api: TestClient, mundo: Mundo, migrated: Engine
) -> None:
    zona = construir_zona(api, mundo, migrated)
    api.post(f"/api/v1/alertas/{zona.alerta}/seguir", headers=mundo.t1.headers)
    assert api.post(f"/api/v1/alertas/{zona.alerta}/confirmar", headers=mundo.lider.headers).status_code == 200
    for cuenta in (mundo.lider, mundo.t1):
        [aviso] = [a for a in avisos(api, cuenta) if a["tipo"] == "ALERTA_CONFIRMADA"]
        assert aviso["enlace"] == f"/alertas/{zona.alerta}" and "Trips" in aviso["texto"]
    assert [a for a in avisos(api, mundo.t2) if a["tipo"] == "ALERTA_CONFIRMADA"] == []  # no la sigue


def test_si_dynamodb_falla_la_operacion_sigue(
    api: TestClient, mundo: Mundo, migrated: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def falla(_: Notificacion) -> None:
        raise RuntimeError("DynamoDB no responde")

    monkeypatch.setattr(_EN_MEMORIA, "publicar", falla)
    caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "Pendiente de revisión")
    r = api.patch(f"/api/v1/casos/{caso}/asignacion", json={"tecnico_id": mundo.t1.id}, headers=mundo.lider.headers)
    assert r.status_code == 200 and avisos(api, mundo.t1) == []
