"""GET /casos/{id} y sus partes: evidencias, cuestionario, explicación e historial, con los permisos por rol."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import boto3
import pytest
from botocore.config import Config
from fastapi.testclient import TestClient
from moto import mock_aws
from sqlalchemy import Engine

from app.infrastructure.s3_storage_client import VIGENCIA_URL_SEGUNDOS, S3StorageClient
from app.main import app
from app.modules.panel_api.casos.router import get_s3_opcional
from tests.api.conftest import Cuenta, Mundo
from tests.api.territorio import (
    crear_caso,
    crear_identificacion,
    crear_respuesta,
    registrar_diagnostico,
)

URL = "/api/v1/casos"
PARTES = ["evidencias", "cuestionario", "explicacion", "historial"]
CLAVE = "evidencias/2026/10/{}.webp"


@pytest.fixture
def s3(api: TestClient) -> Iterator[S3StorageClient]:
    with mock_aws():
        crudo = boto3.client("s3", region_name="us-east-1", config=Config(signature_version="s3v4"))
        crudo.create_bucket(Bucket="anuc-test")
        cliente = S3StorageClient(crudo, "anuc-test")
        app.dependency_overrides[get_s3_opcional] = lambda: cliente
        yield cliente


def pedir(api: TestClient, actor: Cuenta, ruta: str) -> Any:
    r = api.get(ruta, headers=actor.headers)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def caso(mundo: Mundo, migrated: Engine) -> str:
    """Caso En revisión de t1, con análisis ambiguo, cuestionario, fotos y datos del productor."""
    id_caso = crear_caso(
        migrated, mundo.cultivo1, "MARACUYA", "En revisión", tecnico=mundo.t1.id,
        enviado_hace=timedelta(days=2), zona="fruto y hojas",
    )  # fmt: skip
    crear_identificacion(
        migrated,
        id_caso,
        "AMBIGUO",
        {"BOTRYTIS": 62, "RONA": 55, "TRIPS": 10},
        detalle={
            "BOTRYTIS": {"porcentaje": 62, "coinciden": ["moho gris", "hojas blandas"], "descartan": []},
            "RONA": {"porcentaje": 55, "coinciden": ["costras en el fruto"], "descartan": ["no hay grietas"]},
            "TRIPS": {"porcentaje": 10, "coinciden": [], "descartan": ["no se ven insectos"]},
        },
        texto="Dos categorías con síntomas parecidos.",
    )
    crear_respuesta(
        migrated, id_caso, "¿Dónde ves las manchas?", "En el fruto",
        fotos=(("FRUTO", CLAVE.format("F1"), True), ("FRUTO", CLAVE.format("F2"), True)), hace=timedelta(hours=3),
    )  # fmt: skip
    crear_respuesta(
        migrated, id_caso, "¿Desde cuándo?", "Hace una semana",
        fotos=(("HOJA", CLAVE.format("H1"), True), ("ENVES", CLAVE.format("E1"), False)), hace=timedelta(hours=2),
    )  # fmt: skip
    return id_caso


# --- permisos y alcance ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("parte", ["", *[f"/{p}" for p in PARTES]])
def test_sin_sesion_401(api: TestClient, caso: str, parte: str) -> None:
    assert api.get(f"{URL}/{caso}{parte}").status_code == 401


@pytest.mark.parametrize("parte", ["", *[f"/{p}" for p in PARTES]])
def test_el_lider_ve_cualquier_caso_y_el_asignado_tambien(api: TestClient, mundo: Mundo, caso: str, parte: str) -> None:
    assert api.get(f"{URL}/{caso}{parte}", headers=mundo.lider.headers).status_code == 200
    assert api.get(f"{URL}/{caso}{parte}", headers=mundo.t1.headers).status_code == 200


@pytest.mark.parametrize("parte", ["", *[f"/{p}" for p in PARTES]])
def test_un_caso_ajeno_es_404_para_el_tecnico(api: TestClient, mundo: Mundo, caso: str, parte: str) -> None:
    """Criterio de aceptación: fuera del alcance responde 404, como si no existiera."""
    r = api.get(f"{URL}/{caso}{parte}", headers=mundo.t2.headers)
    assert r.status_code == 404 and r.json()["codigo"] == "NO_ENCONTRADO"


@pytest.mark.parametrize("parte", ["", *[f"/{p}" for p in PARTES]])
@pytest.mark.parametrize("id_caso", ["CAS-99999999", "PRO-000001", "x", "0241"])
def test_caso_inexistente_404(api: TestClient, mundo: Mundo, parte: str, id_caso: str) -> None:
    assert api.get(f"{URL}/{id_caso}{parte}", headers=mundo.lider.headers).status_code == 404


def test_el_historial_de_un_caso_no_asignado_es_404_aunque_el_tecnico_haya_tenido_otros(
    api: TestClient, mundo: Mundo, caso: str, migrated: Engine
) -> None:
    suyo = crear_caso(migrated, mundo.cultivo2, "MARACUYA", "En revisión", tecnico=mundo.t2.id)
    assert api.get(f"{URL}/{suyo}/historial", headers=mundo.t2.headers).status_code == 200
    assert api.get(f"{URL}/{caso}/historial", headers=mundo.t2.headers).status_code == 404


# --- detalle -----------------------------------------------------------------------------------------------------


def test_el_detalle_consolida_el_caso(api: TestClient, mundo: Mundo, caso: str) -> None:
    d = pedir(api, mundo.lider, f"{URL}/{caso}")
    assert d["id"] == caso and d["codigo"].startswith("#") and d["estado"] == "EN_REVISION"
    assert d["especie"] == "MARACUYA" and d["zona_afectada"] == "fruto y hojas"
    assert d["productor"] == {
        "id": mundo.productor1["id"], "nombre": "Ana Pérez", "celular": "3001112001", "casos_ultimo_anio": 1,
    }  # fmt: skip
    assert d["finca"] == {
        "id": mundo.finca1["id"], "nombre": "La Esperanza", "vereda": "CENTRO", "latitud": 5.25, "longitud": -73.17,
    }  # fmt: skip
    assert d["cultivo"] == {
        "id": mundo.cultivo1, "nombre": "Lote 1", "tipo": "CAMPO_ABIERTO", "especies": ["GULUPA", "MARACUYA"],
    }  # fmt: skip
    assert d["tecnico"] == {"id": mundo.t1.id, "nombre": "Ana Técnica"}
    assert d["cerrado_en"] is None and d["vence_en"] is None and d["diagnostico"] is None


def test_el_detalle_trae_el_resultado_preliminar_con_sus_porcentajes(api: TestClient, mundo: Mundo, caso: str) -> None:
    d = pedir(api, mundo.lider, f"{URL}/{caso}")
    assert d["preliminar"] == {
        "resultado": "AMBIGUO", "categoria": "BOTRYTIS", "porcentaje": 62.0,
        "alternativa": {"categoria": "RONA", "porcentaje": 55.0},
    }  # fmt: skip
    assert d["preliminar_porcentajes"] == [
        {"categoria": "BOTRYTIS", "porcentaje": 62.0},
        {"categoria": "RONA", "porcentaje": 55.0},
        {"categoria": "TRIPS", "porcentaje": 10.0},
    ]
    assert d["preliminar_resumen"] == "Dos categorías con síntomas parecidos."
    assert d["preliminar_coincidencias"] == [
        {"categoria": "BOTRYTIS", "texto": "moho gris; hojas blandas"},
        {"categoria": "RONA", "texto": "costras en el fruto"},
    ]


def test_un_caso_sin_analisis_no_tiene_preliminar(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    id_caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "En análisis")
    d = pedir(api, mundo.lider, f"{URL}/{id_caso}")
    assert d["preliminar"] is None and d["preliminar_porcentajes"] == [] and d["preliminar_resumen"] is None
    assert d["enviado_a_panel_en"] is None and d["tecnico"] is None
    assert d["evidencias_resumen"] == {"total": 0, "maximo": 12, "por_tipo": {}}
    assert d["cuestionario_resumen"] == {"respuestas": 0, "desde": None, "hasta": None}


def test_los_resumenes_de_evidencias_y_cuestionario(api: TestClient, mundo: Mundo, caso: str) -> None:
    d = pedir(api, mundo.lider, f"{URL}/{caso}")
    assert d["evidencias_resumen"] == {
        "total": 3,
        "maximo": 12,
        "por_tipo": {"FRUTO": 2, "HOJA": 1},
    }  # la foto no útil no cuenta
    assert d["cuestionario_resumen"]["respuestas"] == 2
    assert d["cuestionario_resumen"]["desde"] < d["cuestionario_resumen"]["hasta"]
    assert d["historial_ultimo"]["titulo"] == "Caso creado"  # el caso se creó directo en la base: solo tiene ese evento


def test_los_casos_del_productor_en_el_ultimo_anio(api: TestClient, mundo: Mundo, caso: str, migrated: Engine) -> None:
    crear_caso(migrated, mundo.cultivo1, "GULUPA", "Cerrado", enviado_hace=timedelta(days=100))
    crear_caso(migrated, mundo.cultivo1, "GULUPA", "Cerrado", dias_atras=500, enviado_hace=timedelta(days=500))
    assert pedir(api, mundo.lider, f"{URL}/{caso}")["productor"]["casos_ultimo_anio"] == 2


def test_el_diagnostico_vacio_no_cuenta_como_registrado(api: TestClient, mundo: Mundo, caso: str) -> None:
    """La fila nace vacía al iniciar el caso (D-05); solo existe diagnóstico cuando hay resultado."""
    assert pedir(api, mundo.t1, f"{URL}/{caso}")["diagnostico"] is None


def test_el_diagnostico_registrado_sale_con_su_autor(
    api: TestClient, mundo: Mundo, caso: str, migrated: Engine
) -> None:
    registrar_diagnostico(migrated, caso, "CONFIRMADO", "BOTRYTIS", "Moho gris confirmado")
    d = pedir(api, mundo.lider, f"{URL}/{caso}")["diagnostico"]
    assert (d["resultado_tecnico"], d["categoria_determinada"], d["descripcion"]) == (
        "CONFIRMADO",
        "BOTRYTIS",
        "Moho gris confirmado",
    )
    assert d["tecnico_responsable"] == {"id": mundo.t1.id, "nombre": "Ana Técnica"}
    assert d["motivo_visita"] is None and d["observaciones"] == "" and d["registrado_en"]


def test_el_detalle_no_expone_datos_internos(api: TestClient, mundo: Mundo, caso: str) -> None:
    cuerpo = api.get(f"{URL}/{caso}", headers=mundo.lider.headers).text
    for oculto in ("573001112", "telefono_productor", "cognito_sub", mundo.t1.sub, CLAVE.format("F1")):
        assert oculto not in cuerpo


# --- matriz rol × estado × acción --------------------------------------------------------------------------------

# (estado, tiene técnico t1) → (líder: diagnosticar, cerrar, asignar) y (t1 asignado: diagnosticar, cerrar, asignar)
MATRIZ = [
    ("Caso creado", False, (False, False, False), None),
    ("En análisis", False, (False, False, False), None),
    ("Pendiente de revisión", False, (False, False, True), None),
    ("En revisión", True, (False, False, True), (True, False, False)),
    ("Registrado", True, (False, True, False), (False, True, False)),
    ("Cerrado", True, (False, False, False), (False, False, False)),
    ("Descartado", False, (False, False, False), None),
]


@pytest.mark.parametrize(("estado", "con_tecnico", "lider", "tecnico"), MATRIZ)
def test_permisos_por_rol_y_estado(
    api: TestClient,
    mundo: Mundo,
    migrated: Engine,
    estado: str,
    con_tecnico: bool,
    lider: tuple[bool, ...],
    tecnico: Any,
) -> None:
    id_caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", estado, tecnico=mundo.t1.id if con_tecnico else None)

    def permisos(actor: Cuenta) -> tuple[bool, bool, bool]:
        d = pedir(api, actor, f"{URL}/{id_caso}")
        return d["puede_diagnosticar"], d["puede_cerrar"], d["puede_asignar"]

    assert permisos(mundo.lider) == lider
    if tecnico is None:
        assert api.get(f"{URL}/{id_caso}", headers=mundo.t1.headers).status_code == 404  # sin asignar no lo ve
    else:
        assert permisos(mundo.t1) == tecnico


def test_el_lider_nunca_puede_diagnosticar(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    """D-02: ni siquiera si el caso está asignado a una cuenta suya; para eso tiene su cuenta de técnico."""
    for estado in ("En revisión", "Registrado"):
        id_caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", estado, tecnico=mundo.t1.id)
        assert pedir(api, mundo.lider, f"{URL}/{id_caso}")["puede_diagnosticar"] is False


def test_un_tecnico_distinto_no_diagnostica_ni_cierra(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    id_caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "Registrado", tecnico=mundo.t1.id)
    assert api.get(f"{URL}/{id_caso}", headers=mundo.t2.headers).status_code == 404  # ni siquiera lo ve


def test_los_permisos_se_recalculan_al_reasignar(api: TestClient, mundo: Mundo, caso: str) -> None:
    assert pedir(api, mundo.t1, f"{URL}/{caso}")["puede_diagnosticar"] is True
    api.patch(f"{URL}/{caso}/asignacion", json={"tecnico_id": mundo.t2.id}, headers=mundo.lider.headers)
    assert pedir(api, mundo.t2, f"{URL}/{caso}")["puede_diagnosticar"] is True
    assert api.get(f"{URL}/{caso}", headers=mundo.t1.headers).status_code == 404


# --- evidencias --------------------------------------------------------------------------------------------------


def test_las_evidencias_traen_url_prefirmada_con_expiracion(
    api: TestClient, mundo: Mundo, caso: str, s3: S3StorageClient
) -> None:
    fotos = pedir(api, mundo.lider, f"{URL}/{caso}/evidencias")
    assert [(f["tipo"], f["numero"], f["util"]) for f in fotos] == [
        ("FRUTO", 1, True), ("FRUTO", 2, True), ("HOJA", 1, True), ("ENVES", 1, False),
    ]  # fmt: skip
    primera = fotos[0]
    assert set(primera) == {
        "id",
        "tipo",
        "numero",
        "util",
        "descripcion",
        "pregunta",
        "recibida_en",
        "url",
        "url_expira_en",
    }
    assert primera["id"].startswith("FOT-") and primera["pregunta"] == "¿Dónde ves las manchas?"
    assert primera["descripcion"] == "En el fruto"
    url = urlparse(primera["url"])
    assert url.path.endswith(CLAVE.format("F1")) and "anuc-test" in primera["url"]
    assert parse_qs(url.query)["X-Amz-Expires"] == [str(VIGENCIA_URL_SEGUNDOS)]
    expira = datetime.fromisoformat(primera["url_expira_en"])
    assert abs(expira - (datetime.now(UTC) + timedelta(seconds=VIGENCIA_URL_SEGUNDOS))) < timedelta(seconds=30)


def test_cada_foto_tiene_su_propia_url(api: TestClient, mundo: Mundo, caso: str, s3: S3StorageClient) -> None:
    urls = [f["url"] for f in pedir(api, mundo.lider, f"{URL}/{caso}/evidencias")]
    assert len(set(urls)) == 4 and all(u.startswith("https://") or u.startswith("http://") for u in urls)


def test_la_vigencia_de_la_url_es_corta(s3: S3StorageClient) -> None:
    """Las URLs caducan: la vigencia por defecto son 10 minutos y se puede acortar."""
    assert parse_qs(urlparse(s3.url_prefirmada("x.webp")).query)["X-Amz-Expires"] == ["600"]
    assert parse_qs(urlparse(s3.url_prefirmada("x.webp", expira_segundos=5)).query)["X-Amz-Expires"] == ["5"]


def test_sin_almacenamiento_configurado_las_fotos_salen_sin_url(api: TestClient, mundo: Mundo, caso: str) -> None:
    app.dependency_overrides[get_s3_opcional] = lambda: None
    fotos = pedir(api, mundo.lider, f"{URL}/{caso}/evidencias")
    assert len(fotos) == 4 and all(f["url"] is None and f["url_expira_en"] is None for f in fotos)


def test_un_caso_sin_fotos(api: TestClient, mundo: Mundo, migrated: Engine, s3: S3StorageClient) -> None:
    id_caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "En análisis")
    assert pedir(api, mundo.lider, f"{URL}/{id_caso}/evidencias") == []


# --- cuestionario ------------------------------------------------------------------------------------------------


def test_el_cuestionario_va_en_orden_con_su_rango_de_fechas(api: TestClient, mundo: Mundo, caso: str) -> None:
    c = pedir(api, mundo.t1, f"{URL}/{caso}/cuestionario")
    assert c["respuestas"] == [
        {"pregunta": "¿Dónde ves las manchas?", "respuesta": "En el fruto"},
        {"pregunta": "¿Desde cuándo?", "respuesta": "Hace una semana"},
    ]
    assert c["desde"] < c["hasta"]


def test_cuestionario_vacio(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    id_caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "En análisis")
    assert pedir(api, mundo.lider, f"{URL}/{id_caso}/cuestionario") == {"desde": None, "hasta": None, "respuestas": []}


# --- explicación -------------------------------------------------------------------------------------------------

DETALLE = {
    "TRIPS": {"porcentaje": 80, "coinciden": ["insectos pequeños", "manchas plateadas"], "descartan": []},
    "BOTRYTIS": {"porcentaje": 20, "coinciden": [], "descartan": ["no hay moho gris"]},
    "FUSARIUM": {"porcentaje": 5, "coinciden": [], "descartan": []},
}


def test_explicacion_de_un_resultado_identificado(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    id_caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "Pendiente de revisión")
    crear_identificacion(
        migrated, id_caso, "PRELIMINAR", {"TRIPS": 80, "BOTRYTIS": 20, "FUSARIUM": 5}, DETALLE, "Trips probable."
    )
    e = pedir(api, mundo.lider, f"{URL}/{id_caso}/explicacion")
    assert e["coincide"] == ["insectos pequeños", "manchas plateadas"]
    assert e["descarta"] == [{"categoria": "BOTRYTIS", "texto": "no hay moho gris"}]
    assert e["cercanas"] == [
        {"categoria": "BOTRYTIS", "porcentaje": 20.0},
        {"categoria": "FUSARIUM", "porcentaje": 5.0},
    ]
    assert e["texto"] == "Trips probable." and e["regla"].startswith("Identificado") and "50 %" in e["regla"]


def test_explicacion_de_un_resultado_ambiguo(api: TestClient, mundo: Mundo, caso: str) -> None:
    e = pedir(api, mundo.lider, f"{URL}/{caso}/explicacion")
    assert e["coincide"] == ["moho gris", "hojas blandas"]
    assert e["descarta"] == [
        {"categoria": "RONA", "texto": "no hay grietas"},
        {"categoria": "TRIPS", "texto": "no se ven insectos"},
    ]
    assert e["cercanas"] == [{"categoria": "TRIPS", "porcentaje": 10.0}]  # la otra categoría también superó el umbral
    assert e["regla"].startswith("Ambiguo") and "15 puntos" in e["regla"]


def test_explicacion_de_un_resultado_no_identificado(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    id_caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "Pendiente de revisión")
    crear_identificacion(
        migrated, id_caso, "NO_IDENTIFICADO", {"FUSARIUM": 32, "BOTRYTIS": 20}, None, "Sin coincidencia clara."
    )
    e = pedir(api, mundo.lider, f"{URL}/{id_caso}/explicacion")
    assert e["coincide"] == [] and e["descarta"] == []
    assert e["cercanas"] == [
        {"categoria": "FUSARIUM", "porcentaje": 32.0},
        {"categoria": "BOTRYTIS", "porcentaje": 20.0},
    ]
    assert e["regla"].startswith("No identificado")


def test_sin_analisis_no_hay_explicacion(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    id_caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "En análisis")
    r = api.get(f"{URL}/{id_caso}/explicacion", headers=mundo.lider.headers)
    assert r.status_code == 404 and r.json()["codigo"] == "SIN_ANALISIS"


def test_el_umbral_sale_de_la_configuracion(
    api: TestClient, mundo: Mundo, migrated: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core import config

    id_caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "Pendiente de revisión")
    crear_identificacion(migrated, id_caso, "PRELIMINAR", {"TRIPS": 80, "BOTRYTIS": 20}, DETALLE)
    ajustes = config.get_settings().model_copy(update={"umbral_identificacion_pct": 15})
    monkeypatch.setattr("app.modules.panel_api.casos.service.get_settings", lambda: ajustes)
    e = pedir(api, mundo.lider, f"{URL}/{id_caso}/explicacion")
    assert e["cercanas"] == []  # con el 15 % de umbral, el 20 % de Botrytis ya no es «cercana»
    assert "15 %" in e["regla"]


# --- historial ---------------------------------------------------------------------------------------------------


def test_el_historial_anota_quien_hizo_cada_paso(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    id_caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "Pendiente de revisión", enviado_hace=timedelta(days=1))
    api.patch(f"{URL}/{id_caso}/asignacion", json={"tecnico_id": mundo.t1.id}, headers=mundo.lider.headers)
    api.patch(f"{URL}/{id_caso}/asignacion", json={"tecnico_id": mundo.t2.id}, headers=mundo.lider.headers)
    historial = pedir(api, mundo.lider, f"{URL}/{id_caso}/historial")
    por_tipo = {e["tipo"]: e for e in historial}
    assert set(por_tipo) == {"CASO_CREADO", "TECNICO_ASIGNADO", "TECNICO_REASIGNADO"}
    asignado = por_tipo["TECNICO_ASIGNADO"]
    assert asignado["titulo"] == "Técnico asignado" and asignado["detalle"]
    assert asignado["responsable"] == {"tipo": "LIDER", "id": mundo.lider.id, "nombre": "Laura Líder"}
    assert por_tipo["TECNICO_REASIGNADO"]["responsable"]["nombre"] == "Laura Líder"
    asignaciones = [e["tipo"] for e in historial if e["tipo"].startswith("TECNICO")]
    assert asignaciones == ["TECNICO_ASIGNADO", "TECNICO_REASIGNADO"]  # en orden
    assert set(asignado) == {"tipo", "titulo", "en", "detalle", "responsable"}


def test_el_historial_del_caso_nuevo_tiene_su_creacion(api: TestClient, mundo: Mundo, caso: str) -> None:
    [creado] = [e for e in pedir(api, mundo.t1, f"{URL}/{caso}/historial") if e["tipo"] == "CASO_CREADO"]
    assert creado["titulo"] == "Caso creado" and creado["responsable"]["tipo"] in ("PRODUCTOR", "SISTEMA")


def test_el_historial_no_trae_telefonos(api: TestClient, mundo: Mundo, caso: str) -> None:
    cuerpo = api.get(f"{URL}/{caso}/historial", headers=mundo.lider.headers).text
    assert "3001112001" not in cuerpo and "573001112001" not in cuerpo


def test_las_respuestas_que_solo_llevan_una_foto_no_cuentan_como_cuestionario(
    api: TestClient, mundo: Mundo, migrated: Engine
) -> None:
    """El chatbot cuelga cada foto de una respuesta con la «pregunta» fija de evidencia: no es parte del cuestionario."""
    from app.core.evidencias import PREGUNTA_FOTO

    id_caso = crear_caso(migrated, mundo.cultivo1, "GULUPA", "Pendiente de revisión", enviado_hace=timedelta(hours=1))
    crear_respuesta(migrated, id_caso, "¿Desde cuándo?", "Hace una semana")
    crear_respuesta(migrated, id_caso, PREGUNTA_FOTO, "Fotografía enviada", fotos=(("HOJA", CLAVE.format("X1"), True),))
    cuestionario = pedir(api, mundo.lider, f"{URL}/{id_caso}/cuestionario")
    assert [r["pregunta"] for r in cuestionario["respuestas"]] == ["¿Desde cuándo?"]
    detalle = pedir(api, mundo.lider, f"{URL}/{id_caso}")
    assert detalle["cuestionario_resumen"]["respuestas"] == 1 and detalle["evidencias_resumen"]["total"] == 1
