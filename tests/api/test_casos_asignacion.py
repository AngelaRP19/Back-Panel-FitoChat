"""PATCH /casos/{id}/asignacion: asignar y reasignar (solo el líder), con su evento, auditoría y concurrencia."""

import asyncio
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.core.roles import Rol
from app.main import app
from app.modules.panel_api.casos import repository as repo_casos
from tests.api.conftest import Cuenta, Mundo, NuevaCuenta, auditoria_de, consultar
from tests.api.territorio import crear_caso

URL = "/api/v1/casos"


def asignar(api: TestClient, actor: Cuenta | None, id_caso: str, tecnico_id: str) -> httpx.Response:
    return api.patch(
        f"{URL}/{id_caso}/asignacion", json={"tecnico_id": tecnico_id}, headers=actor.headers if actor else None
    )


def estado_de(motor: Engine, id_caso: str) -> str:
    return str(
        consultar(
            motor,
            "SELECT e.nombre_estado AS n FROM casos c JOIN estados e USING (id_estado) WHERE c.id_caso = :c",
            c=id_caso,
        )[0]["n"]
    )


def asignaciones(motor: Engine, id_caso: str) -> list[dict[str, Any]]:
    return consultar(
        motor, "SELECT * FROM asignaciones WHERE id_caso = :c ORDER BY fecha_asignacion, id_asignacion", c=id_caso
    )


def auditoria_casos(motor: Engine) -> list[dict[str, Any]]:
    """Solo la auditoría de casos: el escenario también audita el alta de productores y fincas."""
    return [a for a in auditoria_de(motor) if a["entidad_afectada"] == "casos"]


def evento(motor: Engine, id_caso: str, tipo: str) -> dict[str, Any]:
    """El evento de ese tipo (la base y Python tienen relojes distintos: no se confía en el orden por fecha)."""
    [fila] = [e for e in eventos(motor, id_caso) if e["tipo_evento"] == tipo]
    return fila


def eventos(motor: Engine, id_caso: str) -> list[dict[str, Any]]:
    return consultar(motor, "SELECT * FROM eventos WHERE id_caso = :c ORDER BY fecha_evento, id_evento", c=id_caso)


@pytest.fixture
def pendiente(mundo: Mundo, migrated: Engine) -> str:
    return crear_caso(migrated, mundo.cultivo1, "GULUPA", "Pendiente de revisión", enviado_hace=timedelta(days=1))


# --- permisos ----------------------------------------------------------------------------------------------------


def test_sin_sesion_401(api: TestClient, mundo: Mundo, pendiente: str) -> None:
    assert asignar(api, None, pendiente, mundo.t1.id).status_code == 401


def test_el_tecnico_no_asigna_403(api: TestClient, mundo: Mundo, pendiente: str, migrated: Engine) -> None:
    r = asignar(api, mundo.t1, pendiente, mundo.t1.id)  # ni siquiera a sí mismo
    assert r.status_code == 403 and r.json()["codigo"] == "SIN_PERMISO"
    assert estado_de(migrated, pendiente) == "Pendiente de revisión" and asignaciones(migrated, pendiente) == []


# --- asignar -----------------------------------------------------------------------------------------------------


def test_asignar_pasa_el_caso_a_en_revision(api: TestClient, mundo: Mundo, pendiente: str, migrated: Engine) -> None:
    r = asignar(api, mundo.lider, pendiente, mundo.t1.id)
    assert r.status_code == 200
    assert r.json()["estado"] == "EN_REVISION" and r.json()["tecnico"] == {"id": mundo.t1.id, "nombre": "Ana Técnica"}
    assert r.json()["id"] == pendiente and r.json()["codigo"].startswith("#")
    assert estado_de(migrated, pendiente) == "En revisión"
    [fila] = asignaciones(migrated, pendiente)
    assert fila["id_tecnico"] == mundo.t1.id and fila["fecha_fin"] is None and fila["id_asignacion"].startswith("ASG-")


def test_asignar_anota_el_evento_con_el_lider_como_autor(
    api: TestClient, mundo: Mundo, pendiente: str, migrated: Engine
) -> None:
    asignar(api, mundo.lider, pendiente, mundo.t1.id)
    e = evento(migrated, pendiente, "TECNICO_ASIGNADO")
    assert e["tipo_evento"] == "TECNICO_ASIGNADO"
    assert (e["actor_tipo"], e["actor_id"]) == ("LIDER", mundo.lider.id)


def test_asignar_se_audita(api: TestClient, mundo: Mundo, pendiente: str, migrated: Engine) -> None:
    asignar(api, mundo.lider, pendiente, mundo.t1.id)
    registro = auditoria_casos(migrated)[-1]
    assert (registro["accion"], registro["entidad_afectada"], registro["origen"]) == ("ASIGNAR_CASO", "casos", "LIDER")
    assert registro["id_lider_tecnico"] == mundo.lider.id and pendiente in registro["descripcion"]
    assert mundo.t1.id in registro["valor_nuevo"]


def test_el_tecnico_empieza_a_ver_el_caso(api: TestClient, mundo: Mundo, pendiente: str) -> None:
    assert api.get(f"{URL}/{pendiente}", headers=mundo.t1.headers).status_code == 404
    asignar(api, mundo.lider, pendiente, mundo.t1.id)
    assert api.get(f"{URL}/{pendiente}", headers=mundo.t1.headers).status_code == 200


def test_el_lider_puede_asignar_el_caso_a_su_cuenta_de_tecnico(
    api: TestClient, mundo: Mundo, pendiente: str, nueva_cuenta: NuevaCuenta
) -> None:
    """D-02: el líder atiende casos con otra cuenta (TEC-, otro correo)."""
    suya = nueva_cuenta(Rol.TECNICO, 7, nombre="Laura (técnica)", correo="laura.tecnica@anuc.org")
    r = asignar(api, mundo.lider, pendiente, suya.id)
    assert r.status_code == 200 and r.json()["tecnico"]["id"] == suya.id


# --- reasignar ---------------------------------------------------------------------------------------------------


@pytest.fixture
def en_revision(api: TestClient, mundo: Mundo, pendiente: str) -> str:
    assert asignar(api, mundo.lider, pendiente, mundo.t1.id).status_code == 200
    return pendiente


def test_reasignar_conserva_el_estado(api: TestClient, mundo: Mundo, en_revision: str, migrated: Engine) -> None:
    r = asignar(api, mundo.lider, en_revision, mundo.t2.id)
    assert r.status_code == 200 and r.json()["estado"] == "EN_REVISION" and r.json()["tecnico"]["id"] == mundo.t2.id
    assert estado_de(migrated, en_revision) == "En revisión"


def test_reasignar_cierra_la_asignacion_anterior_y_deja_una_sola_vigente(
    api: TestClient, mundo: Mundo, en_revision: str, migrated: Engine
) -> None:
    asignar(api, mundo.lider, en_revision, mundo.t2.id)
    anterior, nueva = asignaciones(migrated, en_revision)
    assert (anterior["id_tecnico"], nueva["id_tecnico"]) == (mundo.t1.id, mundo.t2.id)
    assert anterior["fecha_fin"] is not None and nueva["fecha_fin"] is None
    assert anterior["fecha_fin"] == nueva["fecha_asignacion"]  # sin huecos ni solapes


def test_reasignar_anota_su_propio_evento(api: TestClient, mundo: Mundo, en_revision: str, migrated: Engine) -> None:
    asignar(api, mundo.lider, en_revision, mundo.t2.id)
    e = evento(migrated, en_revision, "TECNICO_REASIGNADO")
    assert (e["actor_tipo"], e["actor_id"]) == ("LIDER", mundo.lider.id)
    assert "Ana Técnica" in e["descripcion_evento"] and "Beto Técnico" in e["descripcion_evento"]
    assert [x["tipo_evento"] for x in eventos(migrated, en_revision)].count("TECNICO_ASIGNADO") == 1


def test_reasignar_se_audita_con_el_tecnico_anterior(
    api: TestClient, mundo: Mundo, en_revision: str, migrated: Engine
) -> None:
    asignar(api, mundo.lider, en_revision, mundo.t2.id)
    registro = auditoria_casos(migrated)[-1]
    assert registro["accion"] == "REASIGNAR_CASO"
    assert mundo.t1.id in registro["valor_anterior"] and mundo.t2.id in registro["valor_nuevo"]


def test_el_tecnico_anterior_pierde_el_acceso(api: TestClient, mundo: Mundo, en_revision: str) -> None:
    asignar(api, mundo.lider, en_revision, mundo.t2.id)
    assert api.get(f"{URL}/{en_revision}", headers=mundo.t1.headers).status_code == 404
    assert api.get(f"{URL}/{en_revision}/historial", headers=mundo.t1.headers).status_code == 404


def test_asignar_al_mismo_tecnico_409_y_no_cambia_nada(
    api: TestClient, mundo: Mundo, en_revision: str, migrated: Engine
) -> None:
    antes = len(eventos(migrated, en_revision)), len(auditoria_casos(migrated))
    r = asignar(api, mundo.lider, en_revision, mundo.t1.id)
    assert r.status_code == 409 and r.json()["codigo"] == "CASO_YA_ASIGNADO"
    assert (len(eventos(migrated, en_revision)), len(auditoria_casos(migrated))) == antes
    assert len(asignaciones(migrated, en_revision)) == 1


# --- estados que no admiten asignación ---------------------------------------------------------------------------


@pytest.mark.parametrize("estado", ["Registrado", "Cerrado", "Descartado", "Caso creado", "En análisis"])
def test_fuera_de_pendiente_o_en_revision_409(api: TestClient, mundo: Mundo, migrated: Engine, estado: str) -> None:
    id_caso = crear_caso(
        migrated, mundo.cultivo1, "GULUPA", estado, tecnico=mundo.t1.id if estado in ("Registrado", "Cerrado") else None
    )
    antes = len(asignaciones(migrated, id_caso))
    r = asignar(api, mundo.lider, id_caso, mundo.t2.id)
    assert r.status_code == 409 and r.json()["codigo"] == "TRANSICION_INVALIDA"
    assert estado_de(migrated, id_caso) == estado and len(asignaciones(migrated, id_caso)) == antes
    assert auditoria_casos(migrated) == []


def test_un_caso_cerrado_no_se_reabre_asignandolo(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    id_caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "Cerrado", tecnico=mundo.t1.id)
    assert asignar(api, mundo.lider, id_caso, mundo.t2.id).status_code == 409
    assert estado_de(migrated, id_caso) == "Cerrado"


# --- técnico inválido --------------------------------------------------------------------------------------------


def test_un_tecnico_deshabilitado_no_recibe_casos(
    api: TestClient, mundo: Mundo, pendiente: str, nueva_cuenta: NuevaCuenta, migrated: Engine
) -> None:
    inactivo = nueva_cuenta(Rol.TECNICO, 8, activo=False)
    r = asignar(api, mundo.lider, pendiente, inactivo.id)
    assert r.status_code == 422 and r.json()["campos"] == {"tecnico_id": "Elige un técnico activo."}
    assert estado_de(migrated, pendiente) == "Pendiente de revisión" and asignaciones(migrated, pendiente) == []


@pytest.mark.parametrize("tecnico_id", ["TEC-999999", "LID-000001", "PRO-000001", "x", ""])
def test_cuentas_que_no_son_un_tecnico_activo_422(
    api: TestClient, mundo: Mundo, pendiente: str, migrated: Engine, tecnico_id: str
) -> None:
    """El líder (LID-) tampoco recibe casos con su rol (D-02)."""
    tecnico_id = mundo.lider.id if tecnico_id == "LID-000001" else tecnico_id
    r = asignar(api, mundo.lider, pendiente, tecnico_id)
    assert r.status_code == 422 and "tecnico_id" in r.json()["campos"]
    assert estado_de(migrated, pendiente) == "Pendiente de revisión" and auditoria_casos(migrated) == []


def test_falta_el_tecnico(api: TestClient, mundo: Mundo, pendiente: str) -> None:
    r = api.patch(f"{URL}/{pendiente}/asignacion", json={}, headers=mundo.lider.headers)
    assert r.status_code == 422 and "tecnico_id" in r.json()["campos"]


@pytest.mark.parametrize("id_caso", ["CAS-99999999", "PRO-000001", "x", "0241"])
def test_caso_inexistente_404(api: TestClient, mundo: Mundo, id_caso: str) -> None:
    r = asignar(api, mundo.lider, id_caso, mundo.t1.id)
    assert r.status_code == 404 and r.json()["codigo"] == "NO_ENCONTRADO"


# --- transacción y concurrencia ----------------------------------------------------------------------------------


def test_si_algo_falla_a_mitad_no_queda_nada_a_medias(
    api: TestClient, mundo: Mundo, pendiente: str, migrated: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def explota(*_a: object, **_k: object) -> None:
        raise RuntimeError("base caída")

    monkeypatch.setattr(repo_casos, "pasar_a_estado", explota)
    assert asignar(api, mundo.lider, pendiente, mundo.t1.id).status_code == 500
    assert estado_de(migrated, pendiente) == "Pendiente de revisión"
    assert asignaciones(migrated, pendiente) == [] and auditoria_casos(migrated) == []


def _disparar(id_caso: str, actor: Cuenta, tecnicos: list[str]) -> list[httpx.Response]:
    async def correr() -> list[httpx.Response]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as cliente:
            return await asyncio.gather(
                *(
                    cliente.patch(f"{URL}/{id_caso}/asignacion", json={"tecnico_id": t}, headers=actor.headers)
                    for t in tecnicos
                )
            )

    return asyncio.run(correr())


def test_dos_asignaciones_simultaneas_se_turnan_y_queda_una_vigente(
    api: TestClient, mundo: Mundo, pendiente: str, migrated: Engine
) -> None:
    respuestas = _disparar(pendiente, mundo.lider, [mundo.t1.id, mundo.t2.id])
    assert sorted(r.status_code for r in respuestas) == [200, 200]  # la primera asigna, la segunda reasigna
    filas = asignaciones(migrated, pendiente)
    assert len(filas) == 2 and sum(f["fecha_fin"] is None for f in filas) == 1
    tipos = [e["tipo_evento"] for e in eventos(migrated, pendiente)]
    assert tipos.count("TECNICO_ASIGNADO") == 1 and tipos.count("TECNICO_REASIGNADO") == 1
    assert estado_de(migrated, pendiente) == "En revisión"


def test_dos_asignaciones_simultaneas_al_mismo_tecnico_dejan_una(
    api: TestClient, mundo: Mundo, pendiente: str, migrated: Engine
) -> None:
    respuestas = _disparar(pendiente, mundo.lider, [mundo.t1.id, mundo.t1.id, mundo.t1.id])
    assert sorted(r.status_code for r in respuestas) == [200, 409, 409]
    assert {r.json()["codigo"] for r in respuestas if r.status_code == 409} == {"CASO_YA_ASIGNADO"}
    assert len(asignaciones(migrated, pendiente)) == 1
