"""Avisos al productor por WhatsApp cuando su caso cambia en el panel: asignación, informe del diagnóstico y cierre."""

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.infrastructure.whatsapp_client import FALLIDO, ResultadoEnvio, WhatsAppSimulado
from app.main import app
from app.modules.panel_api.casos.router import get_whatsapp_opcional
from tests.api.conftest import Mundo, consultar
from tests.api.territorio import crear_caso, crear_identificacion

URL = "/api/v1/casos"


@pytest.fixture
def whatsapp(api: TestClient) -> Iterator[WhatsAppSimulado]:
    simulado = WhatsAppSimulado()
    app.dependency_overrides[get_whatsapp_opcional] = lambda: simulado
    yield simulado
    app.dependency_overrides.pop(get_whatsapp_opcional, None)


def avisos(motor: Engine) -> list[dict[str, Any]]:
    return consultar(motor, "SELECT * FROM auditoria WHERE accion = 'AVISO_PRODUCTOR' ORDER BY fecha_auditoria")


def textos(whatsapp: WhatsAppSimulado) -> list[str]:
    return [texto for _, texto in whatsapp.enviados]


def test_al_asignar_se_avisa_que_tecnico_revisa_la_consulta(
    api: TestClient, mundo: Mundo, migrated: Engine, whatsapp: WhatsAppSimulado
) -> None:
    id_caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "Pendiente de revisión", enviado_hace=timedelta(days=1))
    r = api.patch(f"{URL}/{id_caso}/asignacion", json={"tecnico_id": mundo.t1.id}, headers=mundo.lider.headers)
    assert r.status_code == 200, r.text
    [(telefono, texto)] = whatsapp.enviados
    [productor] = consultar(
        migrated, "SELECT telefono_productor AS t FROM productores WHERE id_productor = :p", p=mundo.productor1["id"]
    )
    assert telefono == productor["t"]  # al celular del productor del caso
    assert "Ana Técnica" in texto and "La Esperanza" in texto and texto.startswith("Hola Ana, tu consulta #")
    [registro] = avisos(migrated)
    assert registro["entidad_afectada"] == "casos" and id_caso in registro["descripcion"]
    assert "ASIGNADO" in registro["descripcion"] and "SIMULADO" in registro["descripcion"]
    assert telefono not in str(registro)  # la auditoría no guarda el teléfono

    api.patch(f"{URL}/{id_caso}/asignacion", json={"tecnico_id": mundo.t2.id}, headers=mundo.lider.headers)
    assert textos(whatsapp)[-1].endswith("ahora la revisa el técnico Beto Técnico.")


def test_el_informe_del_diagnostico_llega_con_las_anotaciones_del_tecnico(
    api: TestClient, mundo: Mundo, migrated: Engine, whatsapp: WhatsAppSimulado
) -> None:
    id_caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "En revisión", tecnico=mundo.t1.id)
    crear_identificacion(migrated, id_caso, "PRELIMINAR", {"TRIPS": 80, "BOTRYTIS": 20})
    cuerpo = {
        "resultado_tecnico": "CONFIRMADO",
        "descripcion": "Daño por raspado en hojas jóvenes.",
        "observaciones": "Revisar las plantas vecinas cada semana.",
    }
    r = api.post(f"{URL}/{id_caso}/diagnostico", json=cuerpo, headers=mundo.t1.headers)
    assert r.status_code == 200, r.text
    informe = textos(whatsapp)[-1]
    assert informe.startswith("Informe del técnico sobre tu consulta #")
    for esperado in (
        "La Esperanza",
        "Gulupa",
        "confirmó la identificación preliminar",
        "Problema identificado: Trips",
        "Descripción: Daño por raspado en hojas jóvenes.",
        "Observaciones del técnico: Revisar las plantas vecinas cada semana.",
        "Técnico: Ana Técnica",
    ):
        assert esperado in informe
    assert "INFORME_DIAGNOSTICO" in avisos(migrated)[-1]["descripcion"]


def test_requiere_visita_incluye_el_motivo(
    api: TestClient, mundo: Mundo, migrated: Engine, whatsapp: WhatsAppSimulado
) -> None:
    id_caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "En revisión", tecnico=mundo.t1.id)
    cuerpo = {"resultado_tecnico": "REQUIERE_VISITA", "motivo_visita": "Hay que ver las raíces."}
    api.post(f"{URL}/{id_caso}/diagnostico", json=cuerpo, headers=mundo.t1.headers)
    informe = textos(whatsapp)[-1]
    assert "necesita visitar la finca" in informe and "Visita a la finca: Hay que ver las raíces." in informe
    assert "Problema identificado" not in informe


def test_al_cerrar_se_avisa_al_productor(
    api: TestClient, mundo: Mundo, migrated: Engine, whatsapp: WhatsAppSimulado
) -> None:
    id_caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "Registrado", tecnico=mundo.t1.id)
    assert api.post(f"{URL}/{id_caso}/cierre", headers=mundo.t1.headers).status_code == 200
    assert "quedó cerrada" in textos(whatsapp)[-1] and "CIERRE" in avisos(migrated)[-1]["descripcion"]


def test_si_whatsapp_falla_la_accion_igual_se_hace_y_queda_en_la_auditoria(
    api: TestClient, mundo: Mundo, migrated: Engine, whatsapp: WhatsAppSimulado
) -> None:
    def caido(*_: Any) -> Any:
        raise RuntimeError("caída")

    whatsapp.enviar_texto = caido  # type: ignore[method-assign]
    id_caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "Pendiente de revisión", enviado_hace=timedelta(days=1))
    r = api.patch(f"{URL}/{id_caso}/asignacion", json={"tecnico_id": mundo.t1.id}, headers=mundo.lider.headers)
    assert r.status_code == 200 and avisos(migrated) == []  # no se envió: no hay registro de aviso

    whatsapp.enviar_texto = lambda *_: ResultadoEnvio(FALLIDO, error="Meta respondió 400 (código 131047)")  # type: ignore[method-assign]
    api.patch(f"{URL}/{id_caso}/asignacion", json={"tecnico_id": mundo.t2.id}, headers=mundo.lider.headers)
    [registro] = avisos(migrated)
    assert "FALLIDO" in registro["descripcion"] and "131047" in registro["descripcion"]


def test_sin_whatsapp_configurado_el_panel_funciona_igual(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    app.dependency_overrides[get_whatsapp_opcional] = lambda: None
    try:
        id_caso = crear_caso(
            migrated, mundo.cultivo1, "GULUPA", "Pendiente de revisión", enviado_hace=timedelta(days=1)
        )
        r = api.patch(f"{URL}/{id_caso}/asignacion", json={"tecnico_id": mundo.t1.id}, headers=mundo.lider.headers)
        assert r.status_code == 200 and avisos(migrated) == []
    finally:
        app.dependency_overrides.pop(get_whatsapp_opcional, None)
