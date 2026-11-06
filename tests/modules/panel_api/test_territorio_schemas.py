"""Validadores de productores, fincas y cultivos, y las piezas puras del territorio."""

import pytest
from pydantic import ValidationError

from app.core.catalogos_api import EspecieCultivo, TipoCultivo
from app.modules.panel_api.fincas.router import advertencias
from app.modules.panel_api.fincas.schemas import CultivoEditar, CultivoIn, FincaEditar, FincaIn
from app.modules.panel_api.productores.schemas import ProductorEditar, ProductorIn
from app.modules.panel_api.territorio_comun import nombre_de_vereda, sin_tildes, solo_digitos

FINCA = {
    "nombre": "La Esperanza",
    "vereda": "Centro",
    "latitud": 5.25,
    "longitud": -73.17,
    "productor_id": "PRO-000001",
}


def mensajes(error: ValidationError) -> dict[str, str]:
    return {".".join(map(str, e["loc"])): str(e["msg"]).removeprefix("Value error, ") for e in error.errors()}


# --- productores -------------------------------------------------------------------------------------------------


def test_productor_valido_queda_activo_por_defecto() -> None:
    productor = ProductorIn(nombre="  Ana Torres ", celular="3001234567")
    assert (productor.nombre, productor.activo) == ("Ana Torres", True)


@pytest.mark.parametrize("celular", ["", "573001234567", "+573001234567", "2001234567", "300123456"])
def test_productor_con_celular_invalido(celular: str) -> None:
    with pytest.raises(ValidationError) as exc:
        ProductorIn(nombre="Ana", celular=celular)
    assert mensajes(exc.value)["celular"] == "Escribe un celular de 10 dígitos que empiece por 3"


def test_editar_productor_todo_es_opcional_e_ignora_lo_desconocido() -> None:
    assert ProductorEditar(**{"activo": False}).model_dump(exclude_none=True) == {}  # type: ignore[arg-type]


# --- fincas ------------------------------------------------------------------------------------------------------


def test_finca_valida() -> None:
    finca = FincaIn(**FINCA)  # type: ignore[arg-type]
    assert (finca.vereda, finca.latitud, finca.longitud) == ("Centro", 5.25, -73.17)


def test_la_vereda_se_limpia() -> None:
    assert FincaIn(**{**FINCA, "vereda": "  San   Luis "}).vereda == "San Luis"  # type: ignore[arg-type]


@pytest.mark.parametrize("vereda", ["", "   ", "---", "x" * 101])
def test_vereda_invalida(vereda: str) -> None:
    with pytest.raises(ValidationError) as exc:
        FincaIn(**{**FINCA, "vereda": vereda})  # type: ignore[arg-type]
    assert "vereda" in mensajes(exc.value)


@pytest.mark.parametrize("latitud", [90.1, -90.1, float("nan"), float("inf")])
def test_latitud_fuera_de_rango_geografico(latitud: float) -> None:
    with pytest.raises(ValidationError) as exc:
        FincaIn(**{**FINCA, "latitud": latitud})  # type: ignore[arg-type]
    assert "latitud" in mensajes(exc.value)


@pytest.mark.parametrize("longitud", [180.1, -180.1, float("nan")])
def test_longitud_fuera_de_rango_geografico(longitud: float) -> None:
    with pytest.raises(ValidationError) as exc:
        FincaIn(**{**FINCA, "longitud": longitud})  # type: ignore[arg-type]
    assert "longitud" in mensajes(exc.value)


def test_coordenadas_raras_pero_geograficas_no_se_rechazan() -> None:
    """Fuera de Zetaquirá solo se advierte (guía): el esquema las acepta."""
    assert FincaIn(**{**FINCA, "latitud": 4.0, "longitud": 73.0}).longitud == 73.0  # type: ignore[arg-type]


def test_editar_finca_acepta_cambios_parciales() -> None:
    assert FincaEditar(latitud=5.3).model_dump(exclude_none=True) == {"latitud": 5.3}


def test_editar_finca_con_cultivos_normaliza_las_especies() -> None:
    datos = FincaEditar(cultivos=[{"id": "CUL-000001", "tipo": "INVERNADERO", "especies": ["GULUPA", "GULUPA"]}])  # type: ignore[list-item]
    assert datos.cultivos is not None
    assert datos.cultivos[0].especies == [EspecieCultivo.GULUPA]
    assert datos.cultivos[0].tipo is TipoCultivo.INVERNADERO


# --- cultivos ----------------------------------------------------------------------------------------------------


def test_cultivo_valido_quita_especies_repetidas() -> None:
    cultivo = CultivoIn(nombre=" Lote 1 ", tipo="INVERNADERO", especies=["GULUPA", "MARACUYA", "GULUPA"])  # type: ignore[arg-type]
    assert cultivo.nombre == "Lote 1"
    assert cultivo.especies == [EspecieCultivo.GULUPA, EspecieCultivo.MARACUYA]


def test_el_cultivo_exige_al_menos_una_especie() -> None:
    with pytest.raises(ValidationError) as exc:
        CultivoIn(nombre="Lote", tipo="CAMPO_ABIERTO", especies=[])  # type: ignore[arg-type]
    assert "especies" in mensajes(exc.value)


@pytest.mark.parametrize(("tipo", "especies"), [("SEMILLERO", ["GULUPA"]), ("CAMPO_ABIERTO", ["CURUBA"])])
def test_cultivo_con_codigos_desconocidos(tipo: str, especies: list[str]) -> None:
    with pytest.raises(ValidationError):
        CultivoIn(nombre="Lote", tipo=tipo, especies=especies)  # type: ignore[arg-type]


def test_editar_cultivo_no_admite_quedarse_sin_especies() -> None:
    with pytest.raises(ValidationError):
        CultivoEditar(especies=[])


def test_editar_cultivo_vacio_es_valido() -> None:
    assert CultivoEditar().model_dump(exclude_none=True) == {}


# --- piezas puras ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("escrito", "guardado"),
    [("SAN_LUIS", "San Luis"), ("JURACAMBITA", "Juracambita"), ("San Luis", "San Luis"), ("  el  roble ", "el roble")],
)
def test_nombre_de_vereda(escrito: str, guardado: str) -> None:
    assert nombre_de_vereda(escrito) == guardado


def test_sin_tildes_arma_la_expresion_sql() -> None:
    assert sin_tildes("nombre") == "translate(lower(nombre), 'áéíóúüñ', 'aeiouun')"


def test_solo_digitos() -> None:
    assert solo_digitos("+57 (300) 123-4567") == "573001234567"


@pytest.mark.parametrize(
    ("latitud", "longitud", "cantidad"),
    [
        (5.25, -73.17, 0),
        (5.2, -73.0, 0),
        (5.3, -73.0, 0),
        (4.0, -73.17, 1),
        (5.4, -73.17, 1),
        (5.25, 73.17, 1),
        (0.0, 0.0, 2),
    ],
)
def test_advertencias_de_coordenadas(latitud: float, longitud: float, cantidad: int) -> None:
    assert len(advertencias(latitud, longitud)) == cantidad
