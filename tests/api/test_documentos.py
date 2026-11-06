"""GET /documentos, GET /documentos/{id}/archivo y POST /documentos (pantalla 20 · B-07 · D-18)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.infrastructure.documentos_store import _ARCHIVOS_EN_MEMORIA, DocumentosDesactivados, get_documentos_store
from app.main import app
from tests.api.conftest import Mundo, auditoria_de

URL = "/api/v1/documentos"
PDF = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"


def subir(api: TestClient, actor: Any, contenido: bytes = PDF, **campos: str) -> Any:
    datos = {"titulo": "Manual de buenas prácticas", "tipo": "MANUAL", "categoria": "", **campos}
    return api.post(
        URL, data=datos, files={"archivo": ("manual.pdf", contenido, "application/pdf")}, headers=actor.headers
    )


def subido(api: TestClient, actor: Any, **campos: str) -> Any:
    r = subir(api, actor, **campos)
    assert r.status_code == 201, r.text
    return r.json()


def listar(api: TestClient, actor: Any, **filtros: str) -> Any:
    r = api.get(URL, params=filtros, headers=actor.headers)
    assert r.status_code == 200, r.text
    return r.json()


# --- permisos ----------------------------------------------------------------------------------------------------------


def test_sin_sesion_401(api: TestClient) -> None:
    assert api.get(URL).status_code == 401
    assert api.get(f"{URL}/DOC-1/archivo").status_code == 401
    assert api.post(URL).status_code == 401


def test_el_tecnico_lee_pero_no_sube(api: TestClient, mundo: Mundo) -> None:
    doc = subido(api, mundo.lider)
    assert listar(api, mundo.t1)["manual"]["id"] == doc["id"]
    assert api.get(f"{URL}/{doc['id']}/archivo", headers=mundo.t1.headers).status_code == 200
    r = subir(api, mundo.t1)
    assert r.status_code == 403 and r.json()["codigo"] == "SIN_PERMISO"


# --- subir -------------------------------------------------------------------------------------------------------------


def test_subir_devuelve_el_contrato_y_guarda_el_pdf(api: TestClient, mundo: Mundo, migrated: Engine) -> None:
    doc = subido(api, mundo.lider, titulo="  Ficha de Trips  ", tipo="FICHA_TECNICA", categoria="TRIPS")
    assert set(doc) == {"id", "titulo", "tipo", "categoria", "subido_por", "subido_en"}
    assert doc["id"].startswith("DOC-") and doc["titulo"] == "Ficha de Trips"
    assert doc["categoria"] == "TRIPS" and doc["subido_por"] == "Laura Líder"
    assert _ARCHIVOS_EN_MEMORIA.descargar(f"documentos/{doc['id']}.pdf") == PDF
    assert any(a["accion"] == "SUBIR_DOCUMENTO" for a in auditoria_de(migrated))


@pytest.mark.parametrize(
    ("campos", "contenido", "campo"),
    [
        ({"titulo": "  "}, PDF, "titulo"),
        ({"titulo": "x" * 151}, PDF, "titulo"),
        ({"tipo": "RECETA"}, PDF, "tipo"),
        ({"categoria": "PULGON"}, PDF, "categoria"),
        ({}, b"PK\x03\x04 esto es un zip", "archivo"),
        ({}, b"%PDF-" + b"0" * (20 * 1024 * 1024), "archivo"),
    ],
    ids=["titulo-vacio", "titulo-largo", "tipo", "categoria", "no-es-pdf", "mas-de-20-mb"],
)
def test_subir_invalido_422(
    api: TestClient, mundo: Mundo, campos: dict[str, str], contenido: bytes, campo: str
) -> None:
    r = subir(api, mundo.lider, contenido, **campos)
    assert r.status_code == 422 and r.json()["codigo"] == "VALIDACION" and campo in r.json()["campos"]


def test_subir_sin_archivo_422(api: TestClient, mundo: Mundo) -> None:
    r = api.post(URL, data={"titulo": "Manual", "tipo": "MANUAL"}, headers=mundo.lider.headers)
    assert r.status_code == 422 and r.json()["campos"] == {"archivo": "Elige el archivo PDF."}


def test_mas_de_21_mb_413(api: TestClient, mundo: Mundo) -> None:
    assert subir(api, mundo.lider, b"%PDF-" + b"0" * (21 * 1024 * 1024)).status_code == 413


def test_sin_tabla_en_produccion_subir_responde_503(api: TestClient, mundo: Mundo) -> None:
    app.dependency_overrides[get_documentos_store] = DocumentosDesactivados
    try:
        r = subir(api, mundo.lider)
        assert r.status_code == 503 and r.json()["codigo"] == "ERROR_INTERNO"
        assert listar(api, mundo.lider)["manual"] is None
    finally:
        app.dependency_overrides.pop(get_documentos_store, None)


# --- listar y descargar ------------------------------------------------------------------------------------------------


def test_sin_documentos_trae_las_cinco_categorias(api: TestClient, mundo: Mundo) -> None:
    d = listar(api, mundo.lider)
    assert d["manual"] is None and d["generales"] == []
    assert [c["categoria"] for c in d["categorias"]] == [
        "TRIPS",
        "BOTRYTIS",
        "RONA",
        "FUSARIUM",
        "DEFICIENCIA_MAGNESIO",
    ]
    participa = {c["categoria"]: c["participa"] for c in d["categorias"]}
    assert participa["TRIPS"] is True and participa["BOTRYTIS"] is False  # solo Trips es transmisible
    assert all(c["propagacion"] and c["documentos"] == [] for c in d["categorias"])


def test_agrupa_manual_generales_y_categorias(api: TestClient, mundo: Mundo) -> None:
    viejo = subido(api, mundo.lider, titulo="Manual 2025")
    manual = subido(api, mundo.lider, titulo="Manual 2026")
    libro = subido(api, mundo.lider, titulo="Libro de plagas", tipo="LIBRO_REFERENCIA")
    ficha = subido(api, mundo.lider, titulo="Ficha de Trips", tipo="FICHA_TECNICA", categoria="TRIPS")
    d = listar(api, mundo.lider)
    assert d["manual"]["id"] == manual["id"]
    assert [g["id"] for g in d["generales"]] == [libro["id"], viejo["id"]]
    trips = next(c for c in d["categorias"] if c["categoria"] == "TRIPS")
    assert [x["id"] for x in trips["documentos"]] == [ficha["id"]]


@pytest.mark.parametrize(
    ("filtros", "generales", "categorias", "con_manual"),
    [
        ({"tipo": "FICHA_TECNICA"}, [], ["TRIPS"], False),
        ({"tipo": "MANUAL"}, [], [], True),
        ({"q": "plagas"}, ["Libro de plagas"], [], True),
        ({"q": "viento"}, [], ["TRIPS"], True),  # busca en la propagación
        ({"q": "ROÑA"}, [], ["RONA"], True),  # sin importar mayúsculas
        ({"q": "rona"}, [], ["RONA"], True),  # ni tildes
    ],
)
def test_filtros(
    api: TestClient,
    mundo: Mundo,
    filtros: dict[str, str],
    generales: list[str],
    categorias: list[str],
    con_manual: bool,
) -> None:
    subido(api, mundo.lider)
    subido(api, mundo.lider, titulo="Libro de plagas", tipo="LIBRO_REFERENCIA")
    subido(api, mundo.lider, titulo="Ficha de Trips", tipo="FICHA_TECNICA", categoria="TRIPS")
    d = listar(api, mundo.lider, **filtros)
    assert [g["titulo"] for g in d["generales"]] == generales
    assert [c["categoria"] for c in d["categorias"]] == categorias
    assert (d["manual"] is not None) is con_manual


def test_tipo_desconocido_422(api: TestClient, mundo: Mundo) -> None:
    assert api.get(URL, params={"tipo": "RECETA"}, headers=mundo.lider.headers).status_code == 422


def test_descargar_el_pdf(api: TestClient, mundo: Mundo) -> None:
    doc = subido(api, mundo.lider)
    r = api.get(f"{URL}/{doc['id']}/archivo", headers=mundo.lider.headers)
    assert r.status_code == 200 and r.content == PDF and r.headers["content-type"] == "application/pdf"


def test_descargar_lo_que_no_existe_404(api: TestClient, mundo: Mundo) -> None:
    r = api.get(f"{URL}/DOC-NOEXISTE/archivo", headers=mundo.lider.headers)
    assert r.status_code == 404 and r.json()["codigo"] == "NO_ENCONTRADO"
    doc = subido(api, mundo.lider)
    _ARCHIVOS_EN_MEMORIA.vaciar()  # metadatos sin archivo
    assert api.get(f"{URL}/{doc['id']}/archivo", headers=mundo.lider.headers).status_code == 404
