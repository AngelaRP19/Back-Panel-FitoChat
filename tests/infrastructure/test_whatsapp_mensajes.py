"""Mensajes interactivos: el JSON que espera la Cloud API, los límites de Meta y el texto de respaldo."""

import json

import httpx
import pytest

from app.infrastructure.whatsapp_client import ENVIADO, SIMULADO, WhatsAppCloud, WhatsAppSimulado
from app.infrastructure.whatsapp_mensajes import (
    Botones,
    Lista,
    Opcion,
    PedirUbicacion,
    Ubicacion,
    texto_de,
    texto_visible,
)

SI_NO = (Opcion("si", "Sí"), Opcion("no", "No"))


def test_botones_de_respuesta() -> None:
    assert Botones("¿Aceptas?", SI_NO, pie="ANUC").cuerpo() == {
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": "¿Aceptas?"},
            "footer": {"text": "ANUC"},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "si", "title": "Sí"}},
                    {"type": "reply", "reply": {"id": "no", "title": "No"}},
                ]
            },
        },
    }


def test_los_titulos_largos_se_recortan_al_limite_de_meta() -> None:
    boton = Botones("x", (Opcion("1", "Un título de botón demasiado largo"),)).cuerpo()
    titulo = boton["interactive"]["action"]["buttons"][0]["reply"]["title"]
    assert len(titulo) <= 20 and titulo.endswith("…")
    fila = Lista("x", "Ver", (Opcion("1", "F" * 40, "D" * 100),)).cuerpo()["interactive"]["action"]["sections"][0]
    assert len(fila["rows"][0]["title"]) == 24 and len(fila["rows"][0]["description"]) == 72


@pytest.mark.parametrize("n", [0, 4])
def test_botones_solo_de_uno_a_tres(n: int) -> None:
    with pytest.raises(ValueError):
        Botones("x", tuple(Opcion(str(i), str(i)) for i in range(n)))


def test_lista_con_secciones_y_maximo_diez_filas() -> None:
    lista = Lista("¿Qué finca?", "Ver fincas", (Opcion("1", "La Esperanza", "Centro"), Opcion("nueva", "Nueva")))
    assert lista.cuerpo()["interactive"] == {
        "type": "list",
        "body": {"text": "¿Qué finca?"},
        "action": {
            "button": "Ver fincas",
            "sections": [
                {
                    "title": "Opciones",
                    "rows": [
                        {"id": "1", "title": "La Esperanza", "description": "Centro"},
                        {"id": "nueva", "title": "Nueva"},
                    ],
                }
            ],
        },
    }
    with pytest.raises(ValueError):
        Lista("x", "Ver", tuple(Opcion(str(i), str(i)) for i in range(11)))


def test_pedir_ubicacion_usa_el_boton_nativo() -> None:
    assert PedirUbicacion("Envía la ubicación").cuerpo() == {
        "type": "interactive",
        "interactive": {
            "type": "location_request_message",
            "body": {"text": "Envía la ubicación"},
            "action": {"name": "send_location"},
        },
    }


def test_ubicacion_como_mapa() -> None:
    assert Ubicacion(5.2712, -73.1702, "La Esperanza", "Vereda Centro").cuerpo() == {
        "type": "location",
        "location": {"latitude": 5.2712, "longitude": -73.1702, "name": "La Esperanza", "address": "Vereda Centro"},
    }


def test_texto_de_respaldo_con_las_opciones() -> None:
    assert texto_de(Botones("¿Aceptas?", SI_NO)) == "¿Aceptas?\n(Sí / No)"
    assert texto_de(Lista("¿Qué finca?", "Ver", (Opcion("1", "A"), Opcion("2", "B")))) == "¿Qué finca?\n1. A\n2. B"
    assert "📎" in texto_de(PedirUbicacion("Envía la ubicación"))
    assert texto_de("hola") == "hola" and texto_visible(Botones("¿Aceptas?", SI_NO)) == "¿Aceptas?"


def test_el_simulado_anota_el_texto_visible_y_el_interactivo() -> None:
    simulado = WhatsAppSimulado()
    mensaje = Botones("¿Aceptas?", SI_NO)
    assert simulado.enviar_interactivo("573001112233", mensaje).estado == SIMULADO
    assert simulado.enviados == [("573001112233", "¿Aceptas?")]
    assert simulado.interactivos == [("573001112233", mensaje)]


def test_el_cliente_real_envia_el_interactivo_a_la_graph_api() -> None:
    vistas: list[httpx.Request] = []

    def manejador(peticion: httpx.Request) -> httpx.Response:
        vistas.append(peticion)
        return httpx.Response(200, json={"messages": [{"id": "wamid.INT"}]})

    cliente = WhatsAppCloud("token", "555", "v26.0", httpx.MockTransport(manejador))
    resultado = cliente.enviar_interactivo("573001112233", PedirUbicacion("Envía la ubicación"))
    assert resultado.estado == ENVIADO and resultado.id_externo == "wamid.INT"
    cuerpo = json.loads(vistas[0].content)
    assert cuerpo["messaging_product"] == "whatsapp" and cuerpo["to"] == "573001112233"
    assert cuerpo["type"] == "interactive" and cuerpo["interactive"]["type"] == "location_request_message"


def test_a_los_numeros_de_demostracion_nunca_sale_un_mensaje_real() -> None:
    vistas: list[httpx.Request] = []
    cliente = WhatsAppCloud(
        "token", "555", "v26.0", httpx.MockTransport(lambda p: vistas.append(p) or httpx.Response(200))
    )
    resultado = cliente.enviar_texto("573000000001", "Hola")
    assert resultado.estado == SIMULADO and "demostración" in (resultado.error or "") and vistas == []
    assert cliente.enviar_interactivo("573000000002", PedirUbicacion("x")).estado == SIMULADO and vistas == []
