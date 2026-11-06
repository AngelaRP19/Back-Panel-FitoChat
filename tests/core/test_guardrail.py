import pytest

from app.core.guardrail import MENSAJE_BLOQUEADO, es_seguro, recomienda_tratamiento, usa_terminos_prohibidos


@pytest.mark.parametrize(
    "texto",
    [
        "Revise el envés de las hojas y avise al técnico si ve insectos.",
        "Hay varios casos de Trips cerca de su finca. Inspeccione sus plantas esta semana.",
        "Avise a la ANUC si nota manchas plateadas en los frutos.",
        "Los productores de la vereda deben estar atentos.",  # «productores» no es «producto»
        "Revise 30 plantas por lote.",
    ],
)
def test_los_mensajes_de_vigilancia_pasan(texto: str) -> None:
    assert not recomienda_tratamiento(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "Aplique 2 ml de insecticida por litro",
        "Fumigue las plantas esta tarde",
        "Use un fungicida de cobre",
        "La dosis recomendada es poca",
        "Mezcle 5 g de producto en el agua",
        "Haga un tratamiento preventivo",
        "Rocíe con jabón potásico",
        "Compre un veneno para los insectos",
        "APLIQUE ESTE PRODUCTO",
        "Use 10 litros de caldo bordelés",
        "Aplicación de agroquímicos",
    ],
)
def test_lo_que_receta_se_bloquea(texto: str) -> None:
    assert recomienda_tratamiento(texto)


def test_ignora_tildes_y_mayusculas() -> None:
    assert recomienda_tratamiento("ROCÍE las hojas") and recomienda_tratamiento("rocie las hojas")


def test_el_mensaje_de_error_es_claro() -> None:
    assert "productos, dosis ni tratamientos" in MENSAJE_BLOQUEADO


@pytest.mark.parametrize(
    "texto",
    [
        "Tengo una confianza del 80 %",
        "Hay una probabilidad alta de roña",
        "Es probable que sea trips",
        "Es probablemente Fusarium",
        "Tengo certeza de que es Botrytis",
        "Estoy seguro de que es roña",
        "CONFIANZA ALTA",
    ],
)
def test_los_terminos_de_certeza_se_detectan(texto: str) -> None:
    assert usa_terminos_prohibidos(texto) and not es_seguro(texto)


def test_el_porcentaje_de_coincidencia_de_sintomas_es_seguro() -> None:
    texto = "Coincidencia de síntomas con roña: 72 %. La identificación es preliminar."
    assert not usa_terminos_prohibidos(texto) and es_seguro(texto)


def test_un_texto_seguro_exige_pasar_ambos_filtros() -> None:
    assert not es_seguro("Aplique 2 ml por litro")  # receta
    assert not es_seguro("Probabilidad alta")  # promete certeza
    assert es_seguro("Las manchas coinciden con las de la roña")
