"""GET /resumen/inicio, /resumen/casos-por-categoria y la coherencia de los contadores del Sidebar."""

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tests.api.conftest import Cuenta, Mundo, NuevaCuenta
from tests.api.territorio import crear_alerta, crear_caso, registrar_diagnostico

INICIO = "/api/v1/resumen/inicio"
CATEGORIAS = "/api/v1/resumen/casos-por-categoria"
NAV = "/api/v1/resumen/navegacion"
HORA = timedelta(hours=1)
DIA = timedelta(days=1)


def pedir(api: TestClient, actor: Cuenta, ruta: str, **filtros: Any) -> Any:
    r = api.get(ruta, params=filtros, headers=actor.headers)
    assert r.status_code == 200, r.text
    return r.json()


def sql(motor: Engine, consulta: str, **parametros: Any) -> None:
    with motor.begin() as conexion:
        conexion.execute(text(consulta), parametros)


def cerrar_en(motor: Engine, id_caso: str, hace: str = "0 days", vence: int | None = None) -> None:
    """Pone la fecha de cierre (y el vencimiento en N días) de un caso ya creado como Cerrado."""
    sql(
        motor,
        "UPDATE casos SET fecha_cierre = now() - CAST(:hace AS interval), "
        "vence_en = CASE WHEN CAST(:vence AS integer) IS NULL THEN NULL ELSE CURRENT_DATE + CAST(:vence AS integer) END "
        "WHERE id_caso = :c",
        hace=hace,
        vence=vence,
        c=id_caso,
    )


@pytest.fixture
def datos(mundo: Mundo, migrated: Engine) -> dict[str, str]:
    """
    Pendientes P1 (hace 3 días) y P2 (hace 1 h) · En revisión R1 (t1, hace 5 días) y R2 (t2, hace 60 h) ·
    Registrado G (t1) · Cerrados este mes: C1 (t1, confirmado, vence en 10 días), C2 (t2, otra patología, vence en 20),
    C3 (t1, no identificado) · C4 cerrado hace 40 días (no es de este mes, requiere visita).
    """
    c = mundo.cultivo1
    t1, t2 = mundo.t1.id, mundo.t2.id
    ids = {
        "P1": crear_caso(migrated, c, "GULUPA", "Pendiente de revisión", enviado_hace=3 * DIA),
        "P2": crear_caso(migrated, c, "GULUPA", "Pendiente de revisión", enviado_hace=HORA),
        "R1": crear_caso(migrated, c, "GULUPA", "En revisión", tecnico=t1, enviado_hace=5 * DIA),
        "R2": crear_caso(migrated, c, "GULUPA", "En revisión", tecnico=t2, enviado_hace=60 * HORA),
        "G": crear_caso(migrated, c, "GULUPA", "Registrado", tecnico=t1, enviado_hace=4 * DIA),
        "C1": crear_caso(migrated, c, "GULUPA", "Cerrado", tecnico=t1, enviado_hace=9 * DIA),
        "C2": crear_caso(migrated, c, "GULUPA", "Cerrado", tecnico=t2, enviado_hace=9 * DIA),
        "C3": crear_caso(migrated, c, "GULUPA", "Cerrado", tecnico=t1, enviado_hace=9 * DIA),
        "C4": crear_caso(migrated, c, "GULUPA", "Cerrado", tecnico=t2, enviado_hace=60 * DIA),
    }
    registrar_diagnostico(migrated, ids["G"], "CONFIRMADO", "TRIPS")
    registrar_diagnostico(migrated, ids["C1"], "CONFIRMADO", "TRIPS")
    registrar_diagnostico(migrated, ids["C2"], "OTRA_PATOLOGIA", "BOTRYTIS")
    registrar_diagnostico(migrated, ids["C3"], "NO_IDENTIFICADO", None)
    registrar_diagnostico(migrated, ids["C4"], "REQUIERE_VISITA", None)
    cerrar_en(migrated, ids["C1"], vence=10)
    cerrar_en(migrated, ids["C2"], vence=20)
    cerrar_en(migrated, ids["C3"])
    cerrar_en(migrated, ids["C4"], hace="40 days")
    return ids


# --- permisos ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("ruta", [INICIO, CATEGORIAS, NAV])
def test_sin_sesion_401(api: TestClient, ruta: str) -> None:
    assert api.get(ruta).status_code == 401


def test_la_grafica_por_categoria_es_del_lider(api: TestClient, mundo: Mundo) -> None:
    r = api.get(CATEGORIAS, headers=mundo.t1.headers)
    assert r.status_code == 403 and r.json()["codigo"] == "SIN_PERMISO"


def test_cada_rol_recibe_su_inicio(api: TestClient, mundo: Mundo) -> None:
    assert pedir(api, mundo.lider, INICIO)["rol"] == "LIDER_TECNICO"
    assert pedir(api, mundo.t1, INICIO)["rol"] == "TECNICO"


# --- inicio del líder --------------------------------------------------------------------------------------------


def test_el_inicio_del_lider_sin_datos(api: TestClient, mundo: Mundo) -> None:
    d = pedir(api, mundo.lider, INICIO)
    assert d["kpis"]["pendientes_revision"] == 0 and d["kpis"]["cerrados_mes"]["total"] == 0
    assert d["pendientes_antiguos"] == [] and d["pendientes_sin_tecnico"] == 0
    assert d["alertas"] == {"generadas_sin_revisar": 0, "destacada": None, "recientes": []}
    assert [t["activos"] for t in d["carga_tecnicos"]] == [0, 0]  # los dos técnicos, sin carga
    assert d["generado_en"]


def test_los_indicadores_del_lider(api: TestClient, mundo: Mundo, datos: dict[str, str]) -> None:
    k = pedir(api, mundo.lider, INICIO)["kpis"]
    assert (k["pendientes_revision"], k["pendientes_mas_48h"], k["en_revision"]) == (2, 1, 2)
    assert k["cerrados_mes"] == {
        "total": 3,
        "confirmados": 1,
        "otra_patologia": 1,
        "no_identificado": 1,
        "requiere_visita": 0,
    }
    assert (k["proximos_a_vencer"], k["dias_aviso_vencimiento"]) == (1, 15)  # C1 vence en 10 días; C2, en 20


def test_los_pendientes_se_cuentan_a_partir_de_las_48_horas(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    crear_caso(migrated, mundo.cultivo1, "GULUPA", "Pendiente de revisión", enviado_hace=47 * HORA)
    crear_caso(migrated, mundo.cultivo1, "GULUPA", "Pendiente de revisión", enviado_hace=49 * HORA)
    assert pedir(api, mundo.lider, INICIO)["kpis"]["pendientes_mas_48h"] == 1


def test_un_cierre_de_otro_mes_no_cuenta_en_cerrados_del_mes(
    api: TestClient, mundo: Mundo, datos: dict[str, str]
) -> None:
    assert pedir(api, mundo.lider, INICIO)["kpis"]["cerrados_mes"]["requiere_visita"] == 0  # C4 se cerró hace 40 días


def test_los_pendientes_mas_antiguos_primero(api: TestClient, mundo: Mundo, datos: dict[str, str]) -> None:
    d = pedir(api, mundo.lider, INICIO)
    assert [p["id"] for p in d["pendientes_antiguos"]] == [datos["R1"], datos["P1"], datos["R2"], datos["P2"]]
    primero = d["pendientes_antiguos"][0]
    assert set(primero) == {"id", "codigo", "numero", "productor_nombre", "enviado_a_panel_en", "tecnico_asignado_id"}
    assert primero["tecnico_asignado_id"] == mundo.t1.id and primero["productor_nombre"] == "Ana Pérez"
    assert d["pendientes_antiguos"][1]["tecnico_asignado_id"] is None
    assert d["pendientes_sin_tecnico"] == 2


def test_solo_los_cinco_mas_antiguos(api: TestClient, mundo: Mundo, datos: dict[str, str], migrated: Engine) -> None:
    for _ in range(3):
        crear_caso(migrated, mundo.cultivo1, "GULUPA", "Pendiente de revisión", enviado_hace=30 * HORA)
    assert len(pedir(api, mundo.lider, INICIO)["pendientes_antiguos"]) == 5


def test_la_carga_de_los_tecnicos_activos(
    api: TestClient, mundo: Mundo, datos: dict[str, str], nueva_cuenta: NuevaCuenta
) -> None:
    from app.core.roles import Rol

    nueva_cuenta(Rol.TECNICO, 9, nombre="Inactivo", activo=False)
    carga = pedir(api, mundo.lider, INICIO)["carga_tecnicos"]
    assert carga == [
        {"tecnico_id": mundo.t1.id, "nombre": "Ana Técnica", "activos": 2, "en_revision": 1},  # R1 y G
        {"tecnico_id": mundo.t2.id, "nombre": "Beto Técnico", "activos": 1, "en_revision": 1},  # R2
    ]  # el líder y el deshabilitado no aparecen


def test_las_alertas_del_inicio(api: TestClient, mundo: Mundo, datos: dict[str, str], migrated: Engine) -> None:
    primera = crear_alerta(migrated, [datos["C1"]], "GENERADA")
    confirmada = crear_alerta(migrated, [datos["C2"]], "CONFIRMADA")
    comunicada = crear_alerta(migrated, [datos["C3"]], "COMUNICADA")
    ultima = crear_alerta(migrated, [datos["C1"], datos["C2"]], "GENERADA")
    for p in (mundo.productor1["id"], mundo.productor2["id"]):
        sql(
            migrated,
            "INSERT INTO alerta_destinatarios (id_alerta_destinatario, id_alerta, id_productor, tipo_envio, estado_envio) "
            "VALUES (fn_generar_id('seq_alerta_destinatarios', 'ADE', '-', 12), :a, :p, 'ALERTA', 'SIMULADO')",
            a=comunicada,
            p=p,
        )
    a = pedir(api, mundo.lider, INICIO)["alertas"]
    assert a["generadas_sin_revisar"] == 2
    assert a["destacada"] == {"categoria": "TRIPS", "vereda": "CENTRO"}
    assert [x["id"] for x in a["recientes"]] == [ultima, comunicada, confirmada, primera]
    assert len(a["recientes"]) == 4
    reciente_comunicada = next(x for x in a["recientes"] if x["id"] == comunicada)
    assert reciente_comunicada["productores_notificados"] == 2 and reciente_comunicada["estado"] == "COMUNICADA"
    assert next(x for x in a["recientes"] if x["id"] == ultima)["productores_notificados"] is None
    assert set(a["recientes"][0]) == {
        "id", "codigo", "estado", "categoria", "vereda", "num_casos", "productores_notificados", "generada_en",
    }  # fmt: skip


# --- inicio del técnico ------------------------------------------------------------------------------------------


def test_los_indicadores_del_tecnico_son_solo_los_suyos(api: TestClient, mundo: Mundo, datos: dict[str, str]) -> None:
    d = pedir(api, mundo.t1, INICIO)
    assert (d["nombre"], d["lider_nombre"]) == ("Ana Técnica", "Laura Líder")
    k = d["kpis"]
    assert (k["en_revision"], k["en_revision_mas_48h"]) == (1, 1)  # R1 lleva 5 días
    assert k["registrados_sin_cerrar"] == [datos["G"]]
    assert k["cerrados_mes"] == {
        "total": 2,
        "confirmados": 1,
        "otra_patologia": 0,
        "no_identificado": 1,
        "requiere_visita": 0,
    }
    assert (k["proximos_a_vencer"], k["dias_aviso_vencimiento"]) == (1, 15)
    otro = pedir(api, mundo.t2, INICIO)["kpis"]
    assert (otro["en_revision"], otro["en_revision_mas_48h"], otro["registrados_sin_cerrar"]) == (1, 1, [])
    assert otro["cerrados_mes"]["otra_patologia"] == 1 and otro["proximos_a_vencer"] == 0


def test_por_atender_empieza_por_lo_que_mas_espera(api: TestClient, mundo: Mundo, datos: dict[str, str]) -> None:
    d = pedir(api, mundo.t1, INICIO)
    assert [(p["id"], p["estado"], p["mas_48h"]) for p in d["por_atender"]] == [
        (datos["R1"], "EN_REVISION", True),
        (datos["G"], "REGISTRADO", True),
    ]
    assert set(d["por_atender"][0]) == {"id", "codigo", "productor_nombre", "estado", "enviado_a_panel_en", "mas_48h"}


def test_el_tecnico_no_ve_nada_de_otros(api: TestClient, mundo: Mundo, datos: dict[str, str]) -> None:
    cuerpo = str(pedir(api, mundo.t1, INICIO))
    for ajeno in (datos["R2"], datos["C2"], datos["P1"], "Beto"):
        assert ajeno not in cuerpo


def test_los_diagnosticos_recientes_del_tecnico(
    api: TestClient, mundo: Mundo, datos: dict[str, str], migrated: Engine
) -> None:
    sql(
        migrated,
        "UPDATE diagnosticos SET fecha_diagnostico = now() - interval '3 days' WHERE id_caso = :c",
        c=datos["C1"],
    )
    sql(
        migrated,
        "UPDATE diagnosticos SET fecha_diagnostico = now() - interval '1 days' WHERE id_caso = :c",
        c=datos["G"],
    )
    sql(
        migrated,
        "UPDATE diagnosticos SET fecha_diagnostico = now() - interval '2 days' WHERE id_caso = :c",
        c=datos["C3"],
    )
    recientes = pedir(api, mundo.t1, INICIO)["diagnosticos_recientes"]
    assert [(r["caso_id"], r["resultado_tecnico"], r["categoria"]) for r in recientes] == [
        (datos["G"], "CONFIRMADO", "TRIPS"),
        (datos["C3"], "NO_IDENTIFICADO", None),
        (datos["C1"], "CONFIRMADO", "TRIPS"),
    ]
    assert pedir(api, mundo.t2, INICIO)["diagnosticos_recientes"][0]["categoria"] in ("BOTRYTIS", None)


def test_el_inicio_del_tecnico_sin_casos(api: TestClient, mundo: Mundo) -> None:
    d = pedir(api, mundo.t2, INICIO)
    assert d["por_atender"] == [] and d["diagnosticos_recientes"] == [] and d["kpis"]["registrados_sin_cerrar"] == []


# --- los contadores coinciden ------------------------------------------------------------------------------------


def contadores(api: TestClient, mundo: Mundo) -> dict[str, int]:
    """Lo que dicen el Sidebar, la bandeja, la lista de alertas y el inicio del líder."""
    inicio = pedir(api, mundo.lider, INICIO)
    return {
        "nav_pendientes": pedir(api, mundo.lider, NAV)["casos_pendientes"],
        "bandeja_pendientes": pedir(api, mundo.lider, "/api/v1/casos/conteos")["PENDIENTE_REVISION"],
        "inicio_pendientes": inicio["kpis"]["pendientes_revision"],
        "inicio_sin_tecnico": inicio["pendientes_sin_tecnico"],
        "nav_alertas": pedir(api, mundo.lider, NAV)["alertas_generadas"],
        "lista_alertas": pedir(api, mundo.lider, "/api/v1/alertas")["conteos"]["GENERADA"],
        "inicio_alertas": inicio["alertas"]["generadas_sin_revisar"],
    }


def test_los_contadores_del_sidebar_coinciden_con_la_bandeja_y_las_alertas(
    api: TestClient, mundo: Mundo, datos: dict[str, str], migrated: Engine
) -> None:
    """Criterio de aceptación del módulo."""
    crear_alerta(migrated, [datos["C1"]], "GENERADA")
    crear_alerta(migrated, [datos["C2"]], "GENERADA")
    c = contadores(api, mundo)
    assert c["nav_pendientes"] == c["bandeja_pendientes"] == c["inicio_pendientes"] == c["inicio_sin_tecnico"] == 2
    assert c["nav_alertas"] == c["lista_alertas"] == c["inicio_alertas"] == 2


def test_siguen_coincidiendo_despues_de_actuar(
    api: TestClient, mundo: Mundo, datos: dict[str, str], migrated: Engine
) -> None:
    id_alerta = crear_alerta(migrated, [datos["C1"]], "GENERADA")
    api.patch(f"/api/v1/casos/{datos['P1']}/asignacion", json={"tecnico_id": mundo.t2.id}, headers=mundo.lider.headers)
    api.post(f"/api/v1/alertas/{id_alerta}/confirmar", headers=mundo.lider.headers)
    c = contadores(api, mundo)
    assert c["nav_pendientes"] == c["bandeja_pendientes"] == c["inicio_pendientes"] == 1
    assert c["nav_alertas"] == c["lista_alertas"] == c["inicio_alertas"] == 0


def test_los_contadores_del_tecnico_coinciden_con_su_bandeja(
    api: TestClient, mundo: Mundo, datos: dict[str, str]
) -> None:
    for tecnico in (mundo.t1, mundo.t2):
        nav = pedir(api, tecnico, NAV)
        conteos = pedir(api, tecnico, "/api/v1/casos/conteos")
        inicio = pedir(api, tecnico, INICIO)["kpis"]
        assert nav["casos_pendientes"] == conteos["EN_REVISION"] == inicio["en_revision"]
        assert nav["alertas_generadas"] == 0


# --- casos por categoría -----------------------------------------------------------------------------------------


def total_por_categoria(api: TestClient, actor: Cuenta, **filtros: Any) -> dict[str, int]:
    return {i["categoria"]: i["total"] for i in pedir(api, actor, CATEGORIAS, **filtros)["items"]}


@pytest.fixture
def diagnosticos(mundo: Mundo, migrated: Engine) -> dict[str, str]:
    """Trips ×2 (uno confirmado, otro otra patología) · Botrytis ×1 · Rona hace 60 días · Fusarium hace 400 días."""
    ids = {
        n: crear_caso(migrated, mundo.cultivo1, "GULUPA", "Registrado", tecnico=mundo.t1.id)
        for n in ("a", "b", "c", "d", "e", "f")
    }
    registrar_diagnostico(migrated, ids["a"], "CONFIRMADO", "TRIPS")
    registrar_diagnostico(migrated, ids["b"], "OTRA_PATOLOGIA", "TRIPS")
    registrar_diagnostico(migrated, ids["c"], "CONFIRMADO", "BOTRYTIS")
    registrar_diagnostico(migrated, ids["d"], "CONFIRMADO", "RONA")
    registrar_diagnostico(migrated, ids["e"], "OTRA_PATOLOGIA", "FUSARIUM")
    registrar_diagnostico(migrated, ids["f"], "NO_IDENTIFICADO", None)
    sql(
        migrated,
        "UPDATE diagnosticos SET fecha_diagnostico = now() - interval '60 days' WHERE id_caso = :c",
        c=ids["d"],
    )
    sql(
        migrated,
        "UPDATE diagnosticos SET fecha_diagnostico = now() - interval '400 days' WHERE id_caso = :c",
        c=ids["e"],
    )
    return ids


def test_casos_por_categoria_en_los_ultimos_90_dias(
    api: TestClient, mundo: Mundo, diagnosticos: dict[str, str]
) -> None:
    r = pedir(api, mundo.lider, CATEGORIAS)  # 90d por defecto
    assert r["periodo"] == "90d" and r["total"] == 4
    assert [(i["categoria"], i["total"]) for i in r["items"]] == [
        ("TRIPS", 2), ("BOTRYTIS", 1), ("RONA", 1), ("DEFICIENCIA_MAGNESIO", 0), ("FUSARIUM", 0),
    ]  # fmt: skip


def test_el_periodo_cambia_la_ventana(api: TestClient, mundo: Mundo, diagnosticos: dict[str, str]) -> None:
    assert total_por_categoria(api, mundo.lider, periodo="30d") == {
        "TRIPS": 2, "BOTRYTIS": 1, "RONA": 0, "DEFICIENCIA_MAGNESIO": 0, "FUSARIUM": 0,
    }  # fmt: skip
    anio = total_por_categoria(api, mundo.lider, periodo="anio")
    assert anio["RONA"] == 1 and anio["FUSARIUM"] == 0  # el de hace 400 días es de otro año
    assert pedir(api, mundo.lider, CATEGORIAS, periodo="30d")["total"] == 3


def test_solo_cuentan_los_diagnosticos_confirmados(api: TestClient, mundo: Mundo, diagnosticos: dict[str, str]) -> None:
    assert sum(total_por_categoria(api, mundo.lider).values()) == 4  # el no identificado no entra


def test_sin_diagnosticos_todas_las_categorias_van_en_cero(api: TestClient, mundo: Mundo) -> None:
    r = pedir(api, mundo.lider, CATEGORIAS)
    assert r["total"] == 0 and len(r["items"]) == 5 and all(i["total"] == 0 for i in r["items"])


@pytest.mark.parametrize("periodo", ["7d", "mes", "", "30D"])
def test_periodo_invalido_422(api: TestClient, mundo: Mundo, periodo: str) -> None:
    assert api.get(CATEGORIAS, params={"periodo": periodo}, headers=mundo.lider.headers).status_code == 422
