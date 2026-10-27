"""POST /casos/{id}/diagnostico y /cierre: quién puede, qué estado exige, alertas y transacciones."""

import asyncio
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.main import app
from app.modules.panel_api.casos import repository as repo_casos
from app.modules.panel_api.casos import service as servicio_casos
from tests.api.conftest import Cuenta, Mundo, consultar
from tests.api.territorio import crear_caso, crear_identificacion

URL = "/api/v1/casos"
TRIPS = {"TRIPS": 80, "BOTRYTIS": 20}


def diagnosticar(api: TestClient, actor: Cuenta | None, id_caso: str, **cuerpo: Any) -> httpx.Response:
    cuerpo.setdefault("resultado_tecnico", "NO_IDENTIFICADO")
    return api.post(f"{URL}/{id_caso}/diagnostico", json=cuerpo, headers=actor.headers if actor else None)


def cerrar(api: TestClient, actor: Cuenta | None, id_caso: str) -> httpx.Response:
    return api.post(f"{URL}/{id_caso}/cierre", headers=actor.headers if actor else None)


def estado_de(motor: Engine, id_caso: str) -> str:
    return str(
        consultar(
            motor,
            "SELECT e.nombre_estado AS n FROM casos c JOIN estados e USING (id_estado) WHERE c.id_caso = :c",
            c=id_caso,
        )[0]["n"]
    )


def eventos(motor: Engine, id_caso: str, tipo: str) -> list[dict[str, Any]]:
    return consultar(motor, "SELECT * FROM eventos WHERE id_caso = :c AND tipo_evento = :t", c=id_caso, t=tipo)


def auditoria(motor: Engine, accion: str) -> list[dict[str, Any]]:
    return consultar(motor, "SELECT * FROM auditoria WHERE accion = :a", a=accion)


def diagnostico_en_bd(motor: Engine, id_caso: str) -> dict[str, Any]:
    return consultar(motor, "SELECT * FROM diagnosticos WHERE id_caso = :c", c=id_caso)[0]


def caso_en_revision(
    motor: Engine,
    mundo: Mundo,
    *,
    identificacion: str | None = "PRELIMINAR",
    porcentajes: dict[str, float] | None = None,
) -> str:
    """Caso En revisión de la técnica 1, con la identificación indicada (o ninguna)."""
    id_caso = crear_caso(motor, mundo.cultivo1, "GULUPA", "En revisión", tecnico=mundo.t1.id)
    if identificacion:
        crear_identificacion(motor, id_caso, identificacion, porcentajes or TRIPS)
    return id_caso


@pytest.fixture
def caso(mundo: Mundo, migrated: Engine) -> str:
    return caso_en_revision(migrated, mundo)


# --- permisos y alcance ------------------------------------------------------------------------------------------


def test_sin_sesion_401(api: TestClient, caso: str) -> None:
    assert diagnosticar(api, None, caso).status_code == 401
    assert cerrar(api, None, caso).status_code == 401


def test_el_lider_no_diagnostica_403(api: TestClient, mundo: Mundo, caso: str, migrated: Engine) -> None:
    """D-02: el líder atiende casos con su cuenta de técnico, no con su rol."""
    r = diagnosticar(api, mundo.lider, caso)
    assert r.status_code == 403 and r.json()["codigo"] == "SIN_PERMISO"
    assert diagnosticar(api, mundo.lider, "CAS-99999999").status_code == 403  # tampoco revela si existe
    assert estado_de(migrated, caso) == "En revisión"


def test_un_tecnico_no_asignado_recibe_404(api: TestClient, mundo: Mundo, caso: str, migrated: Engine) -> None:
    r = diagnosticar(api, mundo.t2, caso)
    assert r.status_code == 404 and r.json()["codigo"] == "NO_ENCONTRADO"
    assert estado_de(migrated, caso) == "En revisión"


def test_el_tecnico_de_una_asignacion_ya_cerrada_recibe_404(
    api: TestClient, mundo: Mundo, caso: str, migrated: Engine
) -> None:
    api.patch(f"{URL}/{caso}/asignacion", json={"tecnico_id": mundo.t2.id}, headers=mundo.lider.headers)
    assert diagnosticar(api, mundo.t1, caso).status_code == 404  # la asignación vigente ya es de t2
    assert diagnosticar(api, mundo.t2, caso, resultado_tecnico="NO_IDENTIFICADO").status_code == 200


@pytest.mark.parametrize("id_caso", ["CAS-99999999", "PRO-000001", "x"])
def test_caso_inexistente_404(api: TestClient, mundo: Mundo, id_caso: str) -> None:
    assert diagnosticar(api, mundo.t1, id_caso).status_code == 404
    assert cerrar(api, mundo.t1, id_caso).status_code == 404


# --- el diagnóstico ----------------------------------------------------------------------------------------------


def test_confirmar_la_categoria_propuesta(api: TestClient, mundo: Mundo, caso: str, migrated: Engine) -> None:
    r = diagnosticar(api, mundo.t1, caso, resultado_tecnico="CONFIRMADO", descripcion="Trips en el cultivo")
    assert r.status_code == 200
    d = r.json()
    assert d["estado"] == "REGISTRADO"
    assert (
        d["diagnostico"]["resultado_tecnico"] == "CONFIRMADO" and d["diagnostico"]["categoria_determinada"] == "TRIPS"
    )
    assert d["diagnostico"]["descripcion"] == "Trips en el cultivo"
    assert d["diagnostico"]["tecnico_responsable"] == {"id": mundo.t1.id, "nombre": "Ana Técnica"}
    assert (d["puede_diagnosticar"], d["puede_cerrar"], d["puede_asignar"]) == (False, True, False)
    assert estado_de(migrated, caso) == "Registrado"


def test_se_completa_la_fila_vacia_y_hay_uno_solo_por_caso(
    api: TestClient, mundo: Mundo, caso: str, migrated: Engine
) -> None:
    diagnosticar(api, mundo.t1, caso, resultado_tecnico="CONFIRMADO")
    assert len(consultar(migrated, "SELECT 1 FROM diagnosticos WHERE id_caso = :c", c=caso)) == 1
    fila = diagnostico_en_bd(migrated, caso)
    assert fila["resultado_tecnico"] == "CONFIRMADO" and fila["fecha_diagnostico"] is not None


def test_anota_el_evento_y_audita_con_el_tecnico_como_autor(
    api: TestClient, mundo: Mundo, caso: str, migrated: Engine
) -> None:
    diagnosticar(api, mundo.t1, caso, resultado_tecnico="CONFIRMADO")
    [evento] = eventos(migrated, caso, "DIAGNOSTICO_REGISTRADO")
    assert (evento["actor_tipo"], evento["actor_id"]) == ("TECNICO", mundo.t1.id)
    [registro] = auditoria(migrated, "DIAGNOSTICAR")
    assert (registro["origen"], registro["id_tecnico"], registro["entidad_afectada"]) == (
        "TECNICO",
        mundo.t1.id,
        "diagnosticos",
    )
    assert caso in registro["descripcion"]


def test_las_observaciones_se_guardan_y_los_textos_se_recortan(
    api: TestClient, mundo: Mundo, caso: str, migrated: Engine
) -> None:
    r = diagnosticar(
        api,
        mundo.t1,
        caso,
        resultado_tecnico="CONFIRMADO",
        descripcion="  Descripción  ",
        observaciones=" Revisar en 8 días ",
    )
    assert r.json()["diagnostico"]["descripcion"] == "Descripción"
    assert r.json()["diagnostico"]["observaciones"] == "Revisar en 8 días"
    assert diagnostico_en_bd(migrated, caso)["observaciones_diagnostico"] == "Revisar en 8 días"


def test_textos_vacios_cuentan_como_no_enviados(api: TestClient, mundo: Mundo, caso: str) -> None:
    r = diagnosticar(api, mundo.t1, caso, resultado_tecnico="CONFIRMADO", descripcion="   ", observaciones="")
    assert r.status_code == 200 and r.json()["diagnostico"]["descripcion"] == ""


# --- reglas del resultado (P-04) ---------------------------------------------------------------------------------


def test_confirmar_con_otra_categoria_422(api: TestClient, mundo: Mundo, caso: str, migrated: Engine) -> None:
    r = diagnosticar(api, mundo.t1, caso, resultado_tecnico="CONFIRMADO", categoria_determinada="RONA")
    assert r.status_code == 422 and "categoria_determinada" in r.json()["campos"]
    assert estado_de(migrated, caso) == "En revisión"


def test_confirmar_enviando_la_misma_categoria_es_valido(api: TestClient, mundo: Mundo, caso: str) -> None:
    assert (
        diagnosticar(api, mundo.t1, caso, resultado_tecnico="CONFIRMADO", categoria_determinada="TRIPS").status_code
        == 200
    )


@pytest.mark.parametrize(
    ("identificacion", "porcentajes"),
    [("AMBIGUO", {"BOTRYTIS": 62, "RONA": 55}), ("NO_IDENTIFICADO", {"FUSARIUM": 32}), (None, None)],
)
def test_solo_se_confirma_una_identificacion_preliminar(
    api: TestClient, mundo: Mundo, migrated: Engine, identificacion: str | None, porcentajes: dict[str, float] | None
) -> None:
    id_caso = caso_en_revision(migrated, mundo, identificacion=identificacion, porcentajes=porcentajes)
    r = diagnosticar(api, mundo.t1, id_caso, resultado_tecnico="CONFIRMADO", categoria_determinada="BOTRYTIS")
    assert r.status_code == 422 and "resultado_tecnico" in r.json()["campos"]
    assert estado_de(migrated, id_caso) == "En revisión"


def test_otra_patologia_exige_categoria(api: TestClient, mundo: Mundo, caso: str, migrated: Engine) -> None:
    r = diagnosticar(api, mundo.t1, caso, resultado_tecnico="OTRA_PATOLOGIA")
    assert r.status_code == 422 and r.json()["campos"] == {
        "categoria_determinada": "Selecciona la categoría fitosanitaria que determinaste."
    }
    assert estado_de(migrated, caso) == "En revisión"


def test_otra_patologia_con_categoria(api: TestClient, mundo: Mundo, caso: str, migrated: Engine) -> None:
    r = diagnosticar(api, mundo.t1, caso, resultado_tecnico="OTRA_PATOLOGIA", categoria_determinada="FUSARIUM")
    assert r.status_code == 200 and r.json()["diagnostico"]["categoria_determinada"] == "FUSARIUM"


def test_otra_patologia_sirve_cuando_la_identificacion_fue_ambigua(
    api: TestClient, mundo: Mundo, migrated: Engine
) -> None:
    id_caso = caso_en_revision(migrated, mundo, identificacion="AMBIGUO", porcentajes={"BOTRYTIS": 62, "RONA": 55})
    r = diagnosticar(api, mundo.t1, id_caso, resultado_tecnico="OTRA_PATOLOGIA", categoria_determinada="RONA")
    assert r.status_code == 200 and r.json()["diagnostico"]["categoria_determinada"] == "RONA"


@pytest.mark.parametrize("motivo", [None, "", "   "])
def test_requiere_visita_exige_el_motivo(
    api: TestClient, mundo: Mundo, caso: str, migrated: Engine, motivo: str | None
) -> None:
    cuerpo = {} if motivo is None else {"motivo_visita": motivo}
    r = diagnosticar(api, mundo.t1, caso, resultado_tecnico="REQUIERE_VISITA", **cuerpo)
    assert r.status_code == 422 and r.json()["campos"] == {"motivo_visita": "Escribe el motivo de la visita."}
    assert estado_de(migrated, caso) == "En revisión"


def test_requiere_visita_con_motivo_no_guarda_categoria(
    api: TestClient, mundo: Mundo, caso: str, migrated: Engine
) -> None:
    r = diagnosticar(
        api,
        mundo.t1,
        caso,
        resultado_tecnico="REQUIERE_VISITA",
        motivo_visita="Hay que ver el invernadero",
        categoria_determinada="TRIPS",
    )
    assert r.status_code == 200
    assert r.json()["diagnostico"]["motivo_visita"] == "Hay que ver el invernadero"
    assert r.json()["diagnostico"]["categoria_determinada"] is None
    assert diagnostico_en_bd(migrated, caso)["id_categoria_confirmada"] is None


def test_no_identificado_ignora_categoria_y_motivo(api: TestClient, mundo: Mundo, caso: str) -> None:
    r = diagnosticar(
        api, mundo.t1, caso, resultado_tecnico="NO_IDENTIFICADO", categoria_determinada="TRIPS", motivo_visita="x"
    )
    assert r.status_code == 200
    assert r.json()["diagnostico"]["categoria_determinada"] is None and r.json()["diagnostico"]["motivo_visita"] is None


@pytest.mark.parametrize(
    "cuerpo",
    [
        {},
        {"resultado_tecnico": "TAL_VEZ"},
        {"resultado_tecnico": "CONFIRMADO", "categoria_determinada": "CURUBA"},
        {"resultado_tecnico": "NO_IDENTIFICADO", "descripcion": "x" * 1001},
        {"resultado_tecnico": "NO_IDENTIFICADO", "observaciones": "x" * 1001},
        {"resultado_tecnico": "REQUIERE_VISITA", "motivo_visita": "x" * 501},
    ],
)
def test_cuerpo_invalido_422(
    api: TestClient, mundo: Mundo, caso: str, migrated: Engine, cuerpo: dict[str, Any]
) -> None:
    r = api.post(f"{URL}/{caso}/diagnostico", json=cuerpo, headers=mundo.t1.headers)
    assert r.status_code == 422 and r.json()["codigo"] == "VALIDACION"
    assert estado_de(migrated, caso) == "En revisión"


# --- estados y repeticiones --------------------------------------------------------------------------------------


def test_un_segundo_diagnostico_409_y_no_cambia_el_primero(
    api: TestClient, mundo: Mundo, caso: str, migrated: Engine
) -> None:
    assert diagnosticar(api, mundo.t1, caso, resultado_tecnico="CONFIRMADO", descripcion="Primero").status_code == 200
    r = diagnosticar(api, mundo.t1, caso, resultado_tecnico="NO_IDENTIFICADO", descripcion="Segundo")
    assert r.status_code == 409 and r.json()["codigo"] == "TRANSICION_INVALIDA"
    fila = diagnostico_en_bd(migrated, caso)
    assert (fila["resultado_tecnico"], fila["descripcion_diagnostico"]) == ("CONFIRMADO", "Primero")
    assert len(eventos(migrated, caso, "DIAGNOSTICO_REGISTRADO")) == 1 and len(auditoria(migrated, "DIAGNOSTICAR")) == 1


@pytest.mark.parametrize("estado", ["Registrado", "Cerrado"])
def test_un_caso_con_diagnostico_no_admite_otro(api: TestClient, mundo: Mundo, migrated: Engine, estado: str) -> None:
    id_caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", estado, tecnico=mundo.t1.id)
    r = diagnosticar(api, mundo.t1, id_caso)
    assert r.status_code == 409 and r.json()["codigo"] == "TRANSICION_INVALIDA"
    assert estado_de(migrated, id_caso) == estado


def test_un_caso_pendiente_sin_tecnico_no_se_diagnostica(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    id_caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "Pendiente de revisión")
    assert diagnosticar(api, mundo.t1, id_caso).status_code == 404


def test_dos_diagnosticos_simultaneos_dejan_uno(api: TestClient, mundo: Mundo, caso: str, migrated: Engine) -> None:
    async def disparar() -> list[httpx.Response]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as cliente:
            return await asyncio.gather(
                *(
                    cliente.post(
                        f"{URL}/{caso}/diagnostico",
                        json={"resultado_tecnico": "NO_IDENTIFICADO"},
                        headers=mundo.t1.headers,
                    )
                    for _ in range(3)
                )
            )

    respuestas = asyncio.run(disparar())
    assert sorted(r.status_code for r in respuestas) == [200, 409, 409]
    assert len(eventos(migrated, caso, "DIAGNOSTICO_REGISTRADO")) == 1


def test_si_algo_falla_despues_del_procedimiento_se_deshace_todo(
    api: TestClient, mundo: Mundo, caso: str, migrated: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def explota(*_a: object, **_k: object) -> None:
        raise RuntimeError("base caída")

    monkeypatch.setattr(repo_casos, "evaluar_alerta", explota)
    assert diagnosticar(api, mundo.t1, caso, resultado_tecnico="CONFIRMADO").status_code == 500
    assert estado_de(migrated, caso) == "En revisión"
    assert eventos(migrated, caso, "DIAGNOSTICO_REGISTRADO") == [] and auditoria(migrated, "DIAGNOSTICAR") == []


# --- alertas -----------------------------------------------------------------------------------------------------


def alertas(motor: Engine) -> list[dict[str, Any]]:
    return consultar(motor, "SELECT * FROM alertas")


def casos_de_alerta(motor: Engine) -> int:
    return int(consultar(motor, "SELECT count(*) AS n FROM alerta_casos")[0]["n"])


def tres_casos(motor: Engine, mundo: Mundo, categoria: str) -> list[str]:
    return [caso_en_revision(motor, mundo, porcentajes={categoria: 80, "FUSARIUM": 5}) for _ in range(3)]


def test_la_alerta_nace_al_llegar_al_minimo_de_casos_confirmados_de_trips(
    api: TestClient, mundo: Mundo, migrated: Engine
) -> None:
    ids = tres_casos(migrated, mundo, "TRIPS")
    for id_caso in ids[:2]:
        assert diagnosticar(api, mundo.t1, id_caso, resultado_tecnico="CONFIRMADO").status_code == 200
        assert alertas(migrated) == []  # con menos del mínimo (3) todavía no hay alerta
    assert diagnosticar(api, mundo.t1, ids[2], resultado_tecnico="CONFIRMADO").status_code == 200
    [alerta] = alertas(migrated)
    assert alerta["estado_alerta"] == "GENERADA" and casos_de_alerta(migrated) == 3


def test_la_deficiencia_de_magnesio_nunca_genera_alerta(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    for id_caso in tres_casos(migrated, mundo, "DEFICIENCIA_MAGNESIO"):
        assert diagnosticar(api, mundo.t1, id_caso, resultado_tecnico="CONFIRMADO").status_code == 200
    assert alertas(migrated) == []


def test_los_resultados_sin_categoria_confirmada_no_cuentan(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    ids = tres_casos(migrated, mundo, "TRIPS")
    diagnosticar(api, mundo.t1, ids[0], resultado_tecnico="CONFIRMADO")
    diagnosticar(api, mundo.t1, ids[1], resultado_tecnico="NO_IDENTIFICADO")
    diagnosticar(api, mundo.t1, ids[2], resultado_tecnico="REQUIERE_VISITA", motivo_visita="Ver en campo")
    assert alertas(migrated) == []


def test_otra_patologia_de_una_categoria_transmisible_tambien_cuenta(
    api: TestClient, mundo: Mundo, migrated: Engine
) -> None:
    """El técnico determinó la categoría transmisible por su cuenta: es un caso confirmado técnicamente."""
    ids = [caso_en_revision(migrated, mundo, identificacion=None) for _ in range(3)]
    for id_caso in ids:
        diagnosticar(api, mundo.t1, id_caso, resultado_tecnico="OTRA_PATOLOGIA", categoria_determinada="TRIPS")
    assert len(alertas(migrated)) == 1


def test_un_caso_mas_se_suma_a_la_alerta_abierta_sin_duplicarla(
    api: TestClient, mundo: Mundo, migrated: Engine
) -> None:
    ids = [caso_en_revision(migrated, mundo, porcentajes=TRIPS) for _ in range(4)]
    for id_caso in ids:
        diagnosticar(api, mundo.t1, id_caso, resultado_tecnico="CONFIRMADO")
    assert len(alertas(migrated)) == 1 and casos_de_alerta(migrated) == 4


def test_los_casos_lejanos_no_forman_alerta(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    from sqlalchemy import text

    with migrated.begin() as c:  # la finca 2 queda a unos 110 km: fuera de los 3,5 km
        c.execute(text("UPDATE fincas SET latitud_finca = 6.250000 WHERE id_finca = :f"), {"f": mundo.finca2["id"]})
    cerca = [caso_en_revision(migrated, mundo, porcentajes=TRIPS) for _ in range(2)]
    lejos = crear_caso(migrated, mundo.cultivo2, "MARACUYA", "En revisión", tecnico=mundo.t1.id)
    crear_identificacion(migrated, lejos, "PRELIMINAR", TRIPS)
    for id_caso in [*cerca, lejos]:
        assert diagnosticar(api, mundo.t1, id_caso, resultado_tecnico="CONFIRMADO").status_code == 200
    assert alertas(migrated) == []


# --- cierre ------------------------------------------------------------------------------------------------------


@pytest.fixture
def registrado(api: TestClient, mundo: Mundo, caso: str) -> str:
    assert diagnosticar(api, mundo.t1, caso, resultado_tecnico="CONFIRMADO").status_code == 200
    return caso


def vence_esperado(motor: Engine, meses: int) -> str:
    sql = "SELECT ((now() AT TIME ZONE 'America/Bogota')::date + make_interval(months => :m))::date AS v"
    return consultar(motor, sql, m=meses)[0]["v"].isoformat()


def test_el_lider_cierra_un_caso_registrado(api: TestClient, mundo: Mundo, registrado: str, migrated: Engine) -> None:
    r = cerrar(api, mundo.lider, registrado)
    assert r.status_code == 200
    d = r.json()
    assert d["estado"] == "CERRADO" and d["cerrado_en"] is not None
    assert d["vence_en"] == vence_esperado(migrated, 6)
    assert (d["puede_diagnosticar"], d["puede_cerrar"], d["puede_asignar"]) == (False, False, False)
    assert estado_de(migrated, registrado) == "Cerrado"


def test_el_tecnico_asignado_cierra_su_caso(api: TestClient, mundo: Mundo, registrado: str, migrated: Engine) -> None:
    assert cerrar(api, mundo.t1, registrado).status_code == 200
    [evento] = eventos(migrated, registrado, "CASO_CERRADO")
    assert (evento["actor_tipo"], evento["actor_id"]) == ("TECNICO", mundo.t1.id)
    [registro] = auditoria(migrated, "CERRAR_CASO")
    assert (registro["origen"], registro["id_tecnico"]) == ("TECNICO", mundo.t1.id)


def test_el_cierre_del_lider_queda_a_su_nombre(
    api: TestClient, mundo: Mundo, registrado: str, migrated: Engine
) -> None:
    cerrar(api, mundo.lider, registrado)
    [evento] = eventos(migrated, registrado, "CASO_CERRADO")
    assert (evento["actor_tipo"], evento["actor_id"]) == ("LIDER", mundo.lider.id)
    assert auditoria(migrated, "CERRAR_CASO")[0]["id_lider_tecnico"] == mundo.lider.id


def test_la_conservacion_sale_de_la_configuracion(
    api: TestClient, mundo: Mundo, registrado: str, migrated: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core import config

    ajustes = config.get_settings().model_copy(update={"meses_conservacion": 3})
    monkeypatch.setattr(servicio_casos, "get_settings", lambda: ajustes)
    assert cerrar(api, mundo.lider, registrado).json()["vence_en"] == vence_esperado(migrated, 3)


def test_un_tecnico_ajeno_no_cierra_404(api: TestClient, mundo: Mundo, registrado: str, migrated: Engine) -> None:
    assert cerrar(api, mundo.t2, registrado).status_code == 404
    assert estado_de(migrated, registrado) == "Registrado"


@pytest.mark.parametrize("estado", ["Caso creado", "En análisis", "Pendiente de revisión", "En revisión", "Descartado"])
def test_cerrar_sin_diagnostico_409(api: TestClient, mundo: Mundo, migrated: Engine, estado: str) -> None:
    id_caso = crear_caso(
        migrated, mundo.cultivo1, "GULUPA", estado, tecnico=mundo.t1.id if estado == "En revisión" else None
    )
    r = cerrar(api, mundo.lider, id_caso)
    assert r.status_code == 409 and r.json()["codigo"] == "TRANSICION_INVALIDA"
    assert estado_de(migrated, id_caso) == estado and auditoria(migrated, "CERRAR_CASO") == []


def test_cerrar_dos_veces_409_sin_duplicar_efectos(
    api: TestClient, mundo: Mundo, registrado: str, migrated: Engine
) -> None:
    assert cerrar(api, mundo.lider, registrado).status_code == 200
    r = cerrar(api, mundo.lider, registrado)
    assert r.status_code == 409 and r.json()["codigo"] == "TRANSICION_INVALIDA"
    assert len(eventos(migrated, registrado, "CASO_CERRADO")) == 1 and len(auditoria(migrated, "CERRAR_CASO")) == 1


def test_dos_cierres_simultaneos_dejan_uno(api: TestClient, mundo: Mundo, registrado: str, migrated: Engine) -> None:
    async def disparar() -> list[httpx.Response]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as cliente:
            return await asyncio.gather(
                *(cliente.post(f"{URL}/{registrado}/cierre", headers=mundo.lider.headers) for _ in range(3))
            )

    assert sorted(r.status_code for r in asyncio.run(disparar())) == [200, 409, 409]
    assert len(eventos(migrated, registrado, "CASO_CERRADO")) == 1


def test_un_caso_cerrado_no_admite_ningun_cambio_de_estado(
    api: TestClient, mundo: Mundo, registrado: str, migrated: Engine
) -> None:
    """Los casos no se reabren: ni diagnosticar, ni asignar, ni cerrar de nuevo."""
    cerrar(api, mundo.lider, registrado)
    intentos = [
        diagnosticar(api, mundo.t1, registrado),
        api.patch(f"{URL}/{registrado}/asignacion", json={"tecnico_id": mundo.t2.id}, headers=mundo.lider.headers),
        cerrar(api, mundo.lider, registrado),
        cerrar(api, mundo.t1, registrado),
    ]
    assert [r.status_code for r in intentos] == [409, 409, 409, 409]
    assert {r.json()["codigo"] for r in intentos} == {"TRANSICION_INVALIDA"}
    assert estado_de(migrated, registrado) == "Cerrado"


def test_si_el_cierre_falla_el_caso_sigue_registrado(
    api: TestClient, mundo: Mundo, registrado: str, migrated: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def explota(*_a: object, **_k: object) -> None:
        raise RuntimeError("base caída")

    monkeypatch.setattr(repo_casos, "cerrar_caso", explota)
    assert cerrar(api, mundo.lider, registrado).status_code == 500
    assert estado_de(migrated, registrado) == "Registrado"


# --- el flujo completo -------------------------------------------------------------------------------------------


def test_del_pendiente_al_cerrado(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    id_caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "Pendiente de revisión")
    crear_identificacion(migrated, id_caso, "PRELIMINAR", TRIPS)
    assert api.get(f"{URL}/{id_caso}", headers=mundo.t1.headers).status_code == 404  # aún sin técnico
    assert (
        api.patch(
            f"{URL}/{id_caso}/asignacion", json={"tecnico_id": mundo.t1.id}, headers=mundo.lider.headers
        ).status_code
        == 200
    )
    visto = api.get(f"{URL}/{id_caso}", headers=mundo.t1.headers).json()
    assert (visto["puede_diagnosticar"], visto["puede_cerrar"]) == (True, False)
    assert diagnosticar(api, mundo.t1, id_caso, resultado_tecnico="CONFIRMADO").json()["estado"] == "REGISTRADO"
    assert cerrar(api, mundo.t1, id_caso).json()["estado"] == "CERRADO"

    historial = api.get(f"{URL}/{id_caso}/historial", headers=mundo.t1.headers).json()
    tipos = [e["tipo"] for e in historial if e["tipo"] != "CASO_CREADO"]
    assert tipos == ["TECNICO_ASIGNADO", "DIAGNOSTICO_REGISTRADO", "CASO_CERRADO"]
    quienes = {e["tipo"]: e["responsable"]["nombre"] for e in historial if e["responsable"]}
    assert quienes["DIAGNOSTICO_REGISTRADO"] == "Ana Técnica" and quienes["TECNICO_ASIGNADO"] == "Laura Líder"
