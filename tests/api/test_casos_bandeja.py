"""GET /casos, /casos/conteos y /resumen/navegacion: la bandeja, sus pestañas y los contadores del Sidebar."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.infrastructure.db.models import Alerta
from tests.api.conftest import Cuenta, Mundo
from tests.api.territorio import crear_caso, crear_identificacion

URL = "/api/v1/casos"
DIAS = timedelta(days=1)


@pytest.fixture
def casos(mundo: Mundo, migrated: Engine) -> dict[str, str]:
    """
    A pendiente sin técnico · B en revisión (t1, ambiguo) · C registrado (t2, no identificado) ·
    D cerrado (t1, vence en 10 días) · E en análisis sin enviar · F cerrado (t2, vence en 25 días).
    """
    hoy = date.today()
    ids = {
        "A": crear_caso(migrated, mundo.cultivo1, "GULUPA", "Pendiente de revisión", enviado_hace=1 * DIAS),
        "B": crear_caso(migrated, mundo.cultivo1, "MARACUYA", "En revisión", tecnico=mundo.t1.id, enviado_hace=3 * DIAS),
        "C": crear_caso(migrated, mundo.cultivo2, "MARACUYA", "Registrado", tecnico=mundo.t2.id, enviado_hace=5 * DIAS),
        "D": crear_caso(
            migrated, mundo.cultivo2, "MARACUYA", "Cerrado", tecnico=mundo.t1.id, enviado_hace=40 * DIAS,
            vence_en=hoy + timedelta(days=10),
        ),
        "E": crear_caso(migrated, mundo.cultivo1, "GULUPA", "En análisis"),
        "F": crear_caso(
            migrated, mundo.cultivo2, "MARACUYA", "Cerrado", tecnico=mundo.t2.id, enviado_hace=60 * DIAS,
            vence_en=hoy + timedelta(days=25),
        ),
    }  # fmt: skip
    crear_identificacion(migrated, ids["A"], "PRELIMINAR", {"TRIPS": 80, "BOTRYTIS": 20})
    crear_identificacion(migrated, ids["B"], "AMBIGUO", {"BOTRYTIS": 62, "RONA": 55, "TRIPS": 10})
    crear_identificacion(migrated, ids["C"], "NO_IDENTIFICADO", {"FUSARIUM": 32})
    crear_identificacion(migrated, ids["D"], "PRELIMINAR", {"RONA": 90})
    return ids


def pedir(api: TestClient, actor: Cuenta, ruta: str = URL, **filtros: Any) -> dict[str, Any]:
    r = api.get(ruta, params=filtros, headers=actor.headers)
    assert r.status_code == 200, r.text
    return dict(r.json())


def ids_de(api: TestClient, actor: Cuenta, casos: dict[str, str], **filtros: Any) -> list[str]:
    por_id = {v: k for k, v in casos.items()}
    return [por_id[c["id"]] for c in pedir(api, actor, **filtros)["items"]]


def letras(api: TestClient, actor: Cuenta, casos: dict[str, str], **filtros: Any) -> set[str]:
    return set(ids_de(api, actor, casos, **filtros))


# --- permisos y alcance ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("ruta", [URL, f"{URL}/conteos", "/api/v1/resumen/navegacion"])
def test_sin_sesion_401(api: TestClient, ruta: str) -> None:
    assert api.get(ruta).status_code == 401


def test_el_lider_ve_todos_los_casos(api: TestClient, mundo: Mundo, casos: dict[str, str]) -> None:
    assert letras(api, mundo.lider, casos) == set("ABCDEF")


def test_el_tecnico_solo_ve_sus_asignados(api: TestClient, mundo: Mundo, casos: dict[str, str]) -> None:
    assert letras(api, mundo.t1, casos) == {"B", "D"}
    assert letras(api, mundo.t2, casos) == {"C", "F"}


def test_un_caso_reasignado_sale_de_la_bandeja_del_tecnico_anterior(
    api: TestClient, mundo: Mundo, casos: dict[str, str]
) -> None:
    api.patch(f"{URL}/{casos['B']}/asignacion", json={"tecnico_id": mundo.t2.id}, headers=mundo.lider.headers)
    assert letras(api, mundo.t1, casos) == {"D"}
    assert letras(api, mundo.t2, casos) == {"B", "C", "F"}


def test_el_tecnico_no_amplia_su_alcance_con_filtros(api: TestClient, mundo: Mundo, casos: dict[str, str]) -> None:
    assert letras(api, mundo.t1, casos, tecnico_id=mundo.t2.id) == set()
    assert letras(api, mundo.t1, casos, tecnico_id="sin_asignar") == set()
    assert letras(api, mundo.t1, casos, q="Álvaro") == {"D"}  # solo lo suyo, aunque el filtro abarque más


def test_sin_casos_la_bandeja_viene_vacia(api: TestClient, mundo: Mundo) -> None:
    assert pedir(api, mundo.lider) == {"items": [], "total": 0, "page": 1, "page_size": 20}


# --- forma de cada caso ------------------------------------------------------------------------------------------


def item(api: TestClient, mundo: Mundo, casos: dict[str, str], letra: str) -> dict[str, Any]:
    return next(c for c in pedir(api, mundo.lider)["items"] if c["id"] == casos[letra])


def test_cada_caso_trae_los_campos_del_contrato(api: TestClient, mundo: Mundo, casos: dict[str, str]) -> None:
    b = item(api, mundo, casos, "B")
    assert set(b) == {
        "id", "codigo", "estado", "productor", "finca", "cultivo", "especie", "preliminar", "tecnico",
        "enviado_a_panel_en", "vence_en",
    }  # fmt: skip
    assert b["id"].startswith("CAS-") and b["codigo"] == f"#{int(b['id'][4:]):04d}"
    assert b["estado"] == "EN_REVISION" and b["especie"] == "MARACUYA"
    assert b["productor"] == {"id": mundo.productor1["id"], "nombre": "Ana Pérez", "celular": "3001112001"}
    assert b["finca"] == {"id": mundo.finca1["id"], "nombre": "La Esperanza", "vereda": "CENTRO"}
    assert b["cultivo"] == {"id": mundo.cultivo1, "nombre": "Lote 1"}
    assert b["tecnico"] == {"id": mundo.t1.id, "nombre": "Ana Técnica"}


def test_resultado_identificado(api: TestClient, mundo: Mundo, casos: dict[str, str]) -> None:
    assert item(api, mundo, casos, "A")["preliminar"] == {
        "resultado": "PRELIMINAR", "categoria": "TRIPS", "porcentaje": 80.0, "alternativa": None,
    }  # fmt: skip


def test_resultado_ambiguo_trae_la_segunda_categoria(api: TestClient, mundo: Mundo, casos: dict[str, str]) -> None:
    assert item(api, mundo, casos, "B")["preliminar"] == {
        "resultado": "AMBIGUO",
        "categoria": "BOTRYTIS",
        "porcentaje": 62.0,
        "alternativa": {"categoria": "RONA", "porcentaje": 55.0},
    }


def test_resultado_no_identificado_no_propone_categoria(api: TestClient, mundo: Mundo, casos: dict[str, str]) -> None:
    assert item(api, mundo, casos, "C")["preliminar"] == {
        "resultado": "NO_IDENTIFICADO", "categoria": None, "porcentaje": None, "alternativa": None,
    }  # fmt: skip


def test_sin_analisis_no_hay_preliminar(api: TestClient, mundo: Mundo, casos: dict[str, str]) -> None:
    e = item(api, mundo, casos, "E")
    assert e["preliminar"] is None and e["tecnico"] is None and e["enviado_a_panel_en"] is None


def test_sin_tecnico_el_responsable_es_nulo(api: TestClient, mundo: Mundo, casos: dict[str, str]) -> None:
    assert item(api, mundo, casos, "A")["tecnico"] is None


def test_el_vencimiento_es_una_fecha(api: TestClient, mundo: Mundo, casos: dict[str, str]) -> None:
    assert item(api, mundo, casos, "D")["vence_en"] == (date.today() + timedelta(days=10)).isoformat()
    assert item(api, mundo, casos, "A")["vence_en"] is None


def test_el_celular_sale_con_diez_digitos_y_sin_datos_internos(
    api: TestClient, mundo: Mundo, casos: dict[str, str]
) -> None:
    cuerpo = api.get(URL, headers=mundo.lider.headers).text
    for oculto in ("573001112", "cognito_sub", "telefono_productor", mundo.t1.sub):
        assert oculto not in cuerpo


# --- orden y paginación ------------------------------------------------------------------------------------------


def test_los_mas_recientes_primero_y_los_sin_enviar_al_final(
    api: TestClient, mundo: Mundo, casos: dict[str, str]
) -> None:
    assert ids_de(api, mundo.lider, casos) == ["A", "B", "C", "D", "F", "E"]


def test_pagina_y_cuenta_el_total(api: TestClient, mundo: Mundo, casos: dict[str, str]) -> None:
    paginas = [pedir(api, mundo.lider, page=p, page_size=4) for p in (1, 2, 3)]
    assert [x["total"] for x in paginas] == [6, 6, 6]
    assert [len(x["items"]) for x in paginas] == [4, 2, 0]
    ids = [c["id"] for x in paginas for c in x["items"]]
    assert len(ids) == len(set(ids)) == 6


def test_pagina_fuera_de_rango_informa_el_total(api: TestClient, mundo: Mundo, casos: dict[str, str]) -> None:
    assert pedir(api, mundo.lider, page=9, page_size=5) == {"items": [], "total": 6, "page": 9, "page_size": 5}


# --- filtros -----------------------------------------------------------------------------------------------------


def test_filtra_por_estado(api: TestClient, mundo: Mundo, casos: dict[str, str]) -> None:
    assert letras(api, mundo.lider, casos, estado="EN_REVISION") == {"B"}
    assert letras(api, mundo.lider, casos, estado="CERRADO") == {"D", "F"}
    assert letras(api, mundo.lider, casos, estado="DESCARTADO") == set()


def test_busca_por_productor_o_finca_sin_tildes(api: TestClient, mundo: Mundo, casos: dict[str, str]) -> None:
    assert letras(api, mundo.lider, casos, q="esperanza") == {"A", "B", "E"}
    assert letras(api, mundo.lider, casos, q="alvaro") == {"C", "D", "F"}
    assert letras(api, mundo.lider, casos, q="ROBLE") == {"C", "D", "F"}
    assert letras(api, mundo.lider, casos, q="zzz") == set()


def test_busca_por_el_numero_del_caso(api: TestClient, mundo: Mundo, casos: dict[str, str]) -> None:
    numero = int(casos["B"][4:])
    assert letras(api, mundo.lider, casos, q=f"#{numero:04d}") == {"B"}
    assert letras(api, mundo.lider, casos, q=str(numero)) >= {"B"}


def test_la_busqueda_no_es_inyectable(api: TestClient, mundo: Mundo, casos: dict[str, str]) -> None:
    for q in ("'; DROP TABLE casos; --", "%", "_", "\\", "#"):
        assert api.get(URL, params={"q": q}, headers=mundo.lider.headers).status_code == 200
    assert pedir(api, mundo.lider)["total"] == 6


def test_filtra_por_vereda_finca_y_cultivo(api: TestClient, mundo: Mundo, casos: dict[str, str]) -> None:
    assert letras(api, mundo.lider, casos, vereda="CENTRO") == {"A", "B", "E"}
    assert letras(api, mundo.lider, casos, vereda="SAN_LUIS") == {"C", "D", "F"}
    assert letras(api, mundo.lider, casos, vereda="NO_EXISTE") == set()
    assert letras(api, mundo.lider, casos, finca_id=mundo.finca2["id"]) == {"C", "D", "F"}
    assert letras(api, mundo.lider, casos, cultivo_id=mundo.cultivo1) == {"A", "B", "E"}


def test_filtra_por_especie(api: TestClient, mundo: Mundo, casos: dict[str, str]) -> None:
    assert letras(api, mundo.lider, casos, especie="GULUPA") == {"A", "E"}
    assert letras(api, mundo.lider, casos, especie="MARACUYA") == {"B", "C", "D", "F"}
    assert letras(api, mundo.lider, casos, especie="GRANADILLA") == set()


def test_filtra_por_categoria_propuesta(api: TestClient, mundo: Mundo, casos: dict[str, str]) -> None:
    assert letras(api, mundo.lider, casos, categoria="TRIPS") == {"A"}  # en B, Trips solo llegó al 10 %
    assert letras(api, mundo.lider, casos, categoria="BOTRYTIS") == {"B"}
    assert letras(api, mundo.lider, casos, categoria="RONA") == {"D"}
    assert letras(api, mundo.lider, casos, categoria="FUSARIUM") == set()  # el 32 % no se propone
    assert letras(api, mundo.lider, casos, categoria="NO_IDENTIFICADA") == {"C"}


def test_filtra_por_tecnico_o_sin_asignar(api: TestClient, mundo: Mundo, casos: dict[str, str]) -> None:
    assert letras(api, mundo.lider, casos, tecnico_id=mundo.t1.id) == {"B", "D"}
    assert letras(api, mundo.lider, casos, tecnico_id="sin_asignar") == {"A", "E"}
    assert letras(api, mundo.lider, casos, tecnico_id="TEC-999999") == set()


@pytest.mark.parametrize(
    ("minimo", "esperados"), [(50, {"A", "B", "D"}), (60, {"A", "B", "D"}), (70, {"A", "D"}), (85, {"D"}), (100, set())]
)
def test_filtra_por_coincidencia_minima(
    api: TestClient, mundo: Mundo, casos: dict[str, str], minimo: int, esperados: set[str]
) -> None:
    assert letras(api, mundo.lider, casos, coincidencia_min=minimo) == esperados  # el no identificado nunca cuenta


def test_filtra_por_vencimiento(api: TestClient, mundo: Mundo, casos: dict[str, str]) -> None:
    assert letras(api, mundo.lider, casos, vencimiento="15") == {"D"}
    assert letras(api, mundo.lider, casos, vencimiento="30") == {"D", "F"}


def test_filtra_por_fecha_de_envio_en_hora_de_colombia(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    """23:30 del 9 de octubre en Bogotá es el 10 de octubre en UTC: el filtro cuenta el día de Colombia."""
    ahora = datetime.now(UTC)
    ids = {}
    for letra, instante in {
        "tarde": datetime(2026, 10, 10, 4, 30, tzinfo=UTC),  # 9-oct 23:30 Bogotá
        "madrugada": datetime(2026, 10, 10, 5, 30, tzinfo=UTC),  # 10-oct 00:30 Bogotá
    }.items():
        ids[letra] = crear_caso(
            migrated, mundo.cultivo1, "GULUPA", "Pendiente de revisión", enviado_hace=ahora - instante
        )
    por_id = {v: k for k, v in ids.items()}

    def dia(**f: str) -> set[str]:
        return {por_id[c["id"]] for c in pedir(api, mundo.lider, **f)["items"]}

    assert dia(desde="2026-10-10") == {"madrugada"}
    assert dia(hasta="2026-10-09") == {"tarde"}
    assert dia(desde="2026-10-09", hasta="2026-10-10") == {"tarde", "madrugada"}
    assert dia(desde="2026-10-11") == set()


def test_combina_varios_filtros(api: TestClient, mundo: Mundo, casos: dict[str, str]) -> None:
    assert letras(api, mundo.lider, casos, vereda="SAN_LUIS", estado="CERRADO") == {"D", "F"}
    assert letras(api, mundo.lider, casos, vereda="SAN_LUIS", estado="CERRADO", tecnico_id=mundo.t1.id) == {"D"}
    assert letras(api, mundo.lider, casos, especie="MARACUYA", coincidencia_min=60, q="ana") == {"B"}
    assert letras(api, mundo.lider, casos, vereda="CENTRO", estado="CERRADO") == set()


@pytest.mark.parametrize(
    "parametros",
    [
        {"page": 0},
        {"page_size": 101},
        {"estado": "ABIERTO"},
        {"especie": "CURUBA"},
        {"categoria": "OTRA"},
        {"coincidencia_min": 101},
        {"coincidencia_min": -1},
        {"vencimiento": "extendida"},
        {"vencimiento": "7"},
        {"desde": "ayer"},
        {"hasta": "2026-13-40"},
        {"q": "x" * 101},
    ],
)
def test_filtros_invalidos_422(api: TestClient, mundo: Mundo, parametros: dict[str, object]) -> None:
    r = api.get(URL, params=parametros, headers=mundo.lider.headers)
    assert r.status_code == 422 and r.json()["codigo"] == "VALIDACION"


# --- conteos -----------------------------------------------------------------------------------------------------


def test_conteos_por_estado(api: TestClient, mundo: Mundo, casos: dict[str, str]) -> None:
    assert pedir(api, mundo.lider, f"{URL}/conteos") == {
        "todos": 6, "CREADO": 0, "EN_ANALISIS": 1, "PENDIENTE_REVISION": 1, "EN_REVISION": 1,
        "REGISTRADO": 1, "CERRADO": 2, "DESCARTADO": 0,
    }  # fmt: skip


def test_conteos_del_tecnico_solo_cuentan_lo_suyo(api: TestClient, mundo: Mundo, casos: dict[str, str]) -> None:
    c = pedir(api, mundo.t1, f"{URL}/conteos")
    assert (c["todos"], c["EN_REVISION"], c["CERRADO"], c["PENDIENTE_REVISION"]) == (2, 1, 1, 0)


@pytest.mark.parametrize(
    "filtros",
    [
        {},
        {"vereda": "SAN_LUIS"},
        {"especie": "MARACUYA"},
        {"q": "ana"},
        {"coincidencia_min": 60},
        {"vencimiento": "30"},
    ],
)
def test_los_conteos_coinciden_con_el_listado_de_cada_pestana(
    api: TestClient, mundo: Mundo, casos: dict[str, str], filtros: dict[str, Any]
) -> None:
    """Criterio de aceptación: conteos por estado = listado filtrado con el mismo estado."""
    for actor in (mundo.lider, mundo.t1, mundo.t2):
        conteos = pedir(api, actor, f"{URL}/conteos", **filtros)
        assert conteos["todos"] == pedir(api, actor, **filtros)["total"]
        for estado in (
            "CREADO",
            "EN_ANALISIS",
            "PENDIENTE_REVISION",
            "EN_REVISION",
            "REGISTRADO",
            "CERRADO",
            "DESCARTADO",
        ):
            assert conteos[estado] == pedir(api, actor, estado=estado, **filtros)["total"], (actor.rol, filtros, estado)
        assert conteos["todos"] == sum(conteos[e] for e in conteos if e != "todos")


def test_los_conteos_ignoran_la_paginacion(api: TestClient, mundo: Mundo, casos: dict[str, str]) -> None:
    assert pedir(api, mundo.lider, f"{URL}/conteos", page=3, page_size=1)["todos"] == 6


@pytest.mark.parametrize("parametros", [{"especie": "CURUBA"}, {"coincidencia_min": 500}, {"vencimiento": "extendida"}])
def test_conteos_con_filtros_invalidos_422(api: TestClient, mundo: Mundo, parametros: dict[str, object]) -> None:
    assert api.get(f"{URL}/conteos", params=parametros, headers=mundo.lider.headers).status_code == 422


# --- navegación --------------------------------------------------------------------------------------------------


def alerta(motor: Engine, estado: str) -> None:
    with Session(motor) as sesion:
        id_categoria = sesion.scalar(
            text("SELECT id_categoria FROM categorias_fitosanitarias WHERE nombre_categoria = 'Trips'")
        )
        sesion.add(
            Alerta(
                id_categoria=id_categoria,
                estado_alerta=estado,
                fecha_generacion_alerta=datetime.now(UTC),
                numero_minimo_casos=3,
                distancia_km=Decimal("3.50"),
                intervalo_dias=30,
            )
        )
        sesion.commit()


def test_navegacion_del_lider(api: TestClient, mundo: Mundo, casos: dict[str, str], migrated: Engine) -> None:
    alerta(migrated, "GENERADA")
    alerta(migrated, "GENERADA")
    alerta(migrated, "COMUNICADA")
    r = pedir(api, mundo.lider, "/api/v1/resumen/navegacion")
    assert (r["casos_pendientes"], r["alertas_generadas"]) == (1, 2)  # los pendientes son los casos sin técnico
    assert r["notificaciones_nuevas"] == 0 and r["lider"] == {"nombre": mundo.lider.nombre}


def test_navegacion_del_tecnico_solo_cuenta_lo_suyo(
    api: TestClient, mundo: Mundo, casos: dict[str, str], migrated: Engine
) -> None:
    alerta(migrated, "GENERADA")
    t1 = pedir(api, mundo.t1, "/api/v1/resumen/navegacion")
    assert (t1["casos_pendientes"], t1["alertas_generadas"]) == (1, 0)  # B en revisión
    assert t1["lider"] == {"nombre": mundo.lider.nombre}  # a quién pedirle un caso
    t2 = pedir(api, mundo.t2, "/api/v1/resumen/navegacion")
    assert (t2["casos_pendientes"], t2["alertas_generadas"]) == (0, 0)  # C ya registrado


def test_navegacion_sigue_a_las_asignaciones(api: TestClient, mundo: Mundo, casos: dict[str, str]) -> None:
    api.patch(f"{URL}/{casos['A']}/asignacion", json={"tecnico_id": mundo.t2.id}, headers=mundo.lider.headers)
    assert pedir(api, mundo.lider, "/api/v1/resumen/navegacion")["casos_pendientes"] == 0
    assert pedir(api, mundo.t2, "/api/v1/resumen/navegacion")["casos_pendientes"] == 1


def test_navegacion_vacia(api: TestClient, mundo: Mundo) -> None:
    assert pedir(api, mundo.lider, "/api/v1/resumen/navegacion") == {
        "casos_pendientes": 0,
        "alertas_generadas": 0,
        "notificaciones_nuevas": 0,
        "lider": {"nombre": mundo.lider.nombre},
    }
