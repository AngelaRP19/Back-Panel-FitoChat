import pytest

from app.core import catalogos_api as api
from app.core.catalogos_api import Catalogo, CodigoDesconocido, normalizar_codigo

TODOS = [
    api.ESTADOS_CASO,
    api.CATEGORIAS,
    api.ESPECIES,
    api.ESTADOS_CUENTA,
    api.RESULTADOS_ANALISIS,
    api.RESULTADOS_TECNICOS,
    api.ESTADOS_ALERTA,
    api.TIPOS_ENVIO,
    api.TIPOS_EVIDENCIA,
    api.TIPOS_EVENTO,
    api.TIPOS_CULTIVO,
]


@pytest.mark.parametrize("catalogo", TODOS, ids=lambda c: c.nombre)
def test_cada_codigo_tiene_un_nombre_y_viceversa(catalogo: Catalogo) -> None:  # type: ignore[type-arg]
    assert len(catalogo.nombres) == len(set(catalogo.nombres)) == len(catalogo.codigos)
    for codigo in catalogo.codigos:
        nombre = catalogo.a_nombre(codigo)
        assert catalogo.a_codigo(nombre) == codigo  # ida y vuelta sin pérdida


# --- los valores de la guía §6.2 ---------------------------------------------------------------------------------


def test_estados_del_caso() -> None:
    assert api.ESTADOS_CASO.codigos == [
        "CREADO", "EN_ANALISIS", "PENDIENTE_REVISION", "EN_REVISION", "REGISTRADO", "CERRADO", "DESCARTADO"
    ]  # fmt: skip
    assert api.ESTADOS_CASO.nombres == [
        "Caso creado", "En análisis", "Pendiente de revisión", "En revisión", "Registrado", "Cerrado", "Descartado"
    ]  # fmt: skip


def test_no_existen_los_estados_descartados_por_el_equipo() -> None:
    for sobrante in ("REABIERTO", "ANALISIS_TECNICO"):
        with pytest.raises(CodigoDesconocido):
            api.ESTADOS_CASO.parsear(sobrante)
    assert not api.ESTADOS_CASO.conoce_nombre("Reabierto")
    assert not api.ESTADOS_CASO.conoce_nombre("Análisis técnico")


def test_categorias_y_especies() -> None:
    assert api.CATEGORIAS.nombres == ["Botrytis", "Roña", "Deficiencia de magnesio", "Trips", "Fusarium"]
    assert api.CATEGORIAS.a_codigo("Deficiencia de magnesio") == "DEFICIENCIA_MAGNESIO"
    assert api.CATEGORIAS.a_codigo("Roña") == "RONA"
    assert api.ESPECIES.nombres == ["Gulupa", "Granadilla", "Maracuyá"]
    assert api.ESPECIES.a_codigo("Maracuyá") == "MARACUYA"


def test_codigos_que_se_guardan_tal_cual() -> None:
    assert api.ESTADOS_ALERTA.codigos == [
        "GENERADA",
        "EN_REVISION",
        "CONFIRMADA",
        "COMUNICADA",
        "DESCARTADA",
        "CORREGIDA",
    ]
    assert api.TIPOS_ENVIO.codigos == ["ALERTA", "CORRECCION"]
    assert api.TIPOS_CULTIVO.codigos == ["INVERNADERO", "CAMPO_ABIERTO"]
    assert api.TIPOS_EVIDENCIA.codigos == ["HOJA", "ENVES", "FRUTO", "TALLO"]
    assert api.RESULTADOS_TECNICOS.codigos == ["CONFIRMADO", "OTRA_PATOLOGIA", "NO_IDENTIFICADO", "REQUIERE_VISITA"]
    assert api.RESULTADOS_ANALISIS.codigos == ["PRELIMINAR", "AMBIGUO", "NO_IDENTIFICADO"]
    assert api.ESTADOS_CUENTA.codigos == ["ACTIVO", "DESHABILITADO"]
    assert api.ESTADOS_ALERTA.a_nombre("CORREGIDA") == "CORREGIDA"
    assert len(api.TIPOS_EVENTO.codigos) == 8


# --- entrada y salida --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("basura", ["", "trips", "TRIPS ", "Trips", "PLAGA", "DROP TABLE"])
def test_entrada_desconocida_se_rechaza(basura: str) -> None:
    with pytest.raises(CodigoDesconocido) as exc:
        api.CATEGORIAS.parsear(basura)
    assert exc.value.codigo == basura
    assert exc.value.catalogo == "categoría"


def test_a_nombre_rechaza_codigo_desconocido() -> None:
    with pytest.raises(CodigoDesconocido):
        api.ESTADOS_CASO.a_nombre("REABIERTO")


def test_a_nombre_acepta_el_miembro_del_enum() -> None:
    assert api.ESTADOS_CASO.a_nombre(api.EstadoCaso.CERRADO) == "Cerrado"


@pytest.mark.parametrize(
    ("nombre_bd", "esperado"),
    [
        ("Plaga", "PLAGA"),
        ("Curuba", "CURUBA"),
        ("Fusarium oxysporum", "FUSARIUM_OXYSPORUM"),
        ("Árbol Ñandú", "ARBOL_NANDU"),
    ],
)
def test_salida_desconocida_no_rompe(nombre_bd: str, esperado: str) -> None:
    assert api.CATEGORIAS.a_codigo(nombre_bd) == esperado


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("San Luis", "SAN_LUIS"),
        ("  Juracambita ", "JURACAMBITA"),
        ("Centro", "CENTRO"),
        ("Vereda  La-Esperanza", "VEREDA_LA_ESPERANZA"),
        ("Ñ ú ü", "N_U_U"),
        ("", ""),
        ("%%%", ""),
    ],
)
def test_normalizar_codigo(texto: str, esperado: str) -> None:
    assert normalizar_codigo(texto) == esperado


# --- el constructor protege la coherencia ------------------------------------------------------------------------


def test_un_catalogo_incompleto_no_se_construye() -> None:
    with pytest.raises(ValueError, match="nombre para cada código"):
        Catalogo("incompleto", api.TipoEnvio, {api.TipoEnvio.ALERTA: "Alerta"})


def test_un_catalogo_con_nombres_repetidos_no_se_construye() -> None:
    with pytest.raises(ValueError, match="repite"):
        Catalogo("repetido", api.TipoEnvio, {api.TipoEnvio.ALERTA: "Igual", api.TipoEnvio.CORRECCION: "Igual"})
