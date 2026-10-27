import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from app.core import config
from app.infrastructure import whatsapp_client as wa
from app.infrastructure.whatsapp_client import (
    ENVIADO,
    FALLIDO,
    SIMULADO,
    ErrorDescargaMedia,
    WhatsAppCloud,
    WhatsAppSimulado,
    get_whatsapp_client,
)


def test_el_simulado_no_envia_nada_real_y_lo_dice() -> None:
    cliente = WhatsAppSimulado()
    resultado = cliente.enviar_texto("573001112233", "Hola")
    assert resultado.estado == SIMULADO and resultado.exito and resultado.id_externo == "sim-1"
    assert cliente.enviados == [("573001112233", "Hola")]


def test_cada_envio_tiene_su_propio_id() -> None:
    cliente = WhatsAppSimulado()
    ids = [cliente.enviar_texto(f"57300111223{n}", "x").id_externo for n in range(3)]
    assert ids == ["sim-1", "sim-2", "sim-3"]


def test_un_numero_que_falla_se_informa_sin_lanzar_excepcion() -> None:
    cliente = WhatsAppSimulado(fallan={"573001112233"})
    resultado = cliente.enviar_texto("573001112233", "Hola")
    assert resultado.estado == FALLIDO and not resultado.exito and resultado.error
    assert "573001112233" not in resultado.error  # el error no lleva el teléfono completo
    assert cliente.enviados == []


def test_reiniciar_limpia_el_estado() -> None:
    cliente = WhatsAppSimulado(fallan={"1"})
    cliente.enviar_texto("2", "x")
    cliente.reiniciar()
    assert cliente.enviados == [] and cliente.fallan == set()


def test_en_modo_simulado_se_entrega_el_cliente_simulado() -> None:
    assert isinstance(get_whatsapp_client(), WhatsAppSimulado)


def test_el_simulado_entrega_las_fotos_programadas_y_falla_con_las_demas() -> None:
    cliente = WhatsAppSimulado(medios={"m1": b"foto"})
    assert cliente.descargar_media("m1") == b"foto"
    with pytest.raises(ErrorDescargaMedia):
        cliente.descargar_media("m2")


def test_el_cliente_real_exige_token_y_numero(monkeypatch: pytest.MonkeyPatch) -> None:
    real = config.get_settings().model_copy(
        update={"integracion_whatsapp": "real", "whatsapp_access_token": None, "whatsapp_phone_number_id": None}
    )
    monkeypatch.setattr(wa, "get_settings", lambda: real)
    monkeypatch.setattr(wa, "_REAL", None)
    with pytest.raises(RuntimeError, match="WHATSAPP_ACCESS_TOKEN"):
        get_whatsapp_client()


def test_el_cliente_real_se_crea_una_sola_vez(monkeypatch: pytest.MonkeyPatch) -> None:
    real = config.get_settings().model_copy(
        update={
            "integracion_whatsapp": "real",
            "whatsapp_access_token": SecretStr("tok"),
            "whatsapp_phone_number_id": "999",
            "whatsapp_graph_version": "v99.0",
        }
    )
    monkeypatch.setattr(wa, "get_settings", lambda: real)
    monkeypatch.setattr(wa, "_REAL", None)
    creados: list[tuple[str, str, str]] = []

    def fabrica(token: str, numero: str, version: str) -> WhatsAppCloud:
        creados.append((token, numero, version))
        return WhatsAppCloud(token, numero, version)

    assert get_whatsapp_client(fabrica) is get_whatsapp_client(fabrica)
    assert creados == [("tok", "999", "v99.0")]


# --- Cliente real, con un transporte falso (no sale a internet) --------------------------------------------------


def _cloud(manejador: Callable[[httpx.Request], httpx.Response]) -> tuple[WhatsAppCloud, list[httpx.Request]]:
    vistas: list[httpx.Request] = []

    def envoltura(peticion: httpx.Request) -> httpx.Response:
        vistas.append(peticion)
        return manejador(peticion)

    return WhatsAppCloud("token-secreto", "555", "v21.0", httpx.MockTransport(envoltura)), vistas


def test_envia_texto_a_la_graph_api_con_el_token() -> None:
    cliente, vistas = _cloud(lambda p: httpx.Response(200, json={"messages": [{"id": "wamid.ABC"}]}))
    resultado = cliente.enviar_texto("573001112233", "Hola")
    assert resultado.estado == ENVIADO and resultado.id_externo == "wamid.ABC"
    peticion = vistas[0]
    assert str(peticion.url) == "https://graph.facebook.com/v21.0/555/messages"
    assert peticion.headers["Authorization"] == "Bearer token-secreto"
    assert json.loads(peticion.content) == {
        "messaging_product": "whatsapp",
        "to": "573001112233",
        "type": "text",
        "text": {"body": "Hola", "preview_url": False},
    }


def test_un_rechazo_de_meta_es_fallido_y_no_filtra_el_cuerpo() -> None:
    cliente, _ = _cloud(
        lambda p: httpx.Response(400, json={"error": {"code": 131030, "message": "573001112233 no permitido"}})
    )
    resultado = cliente.enviar_texto("573001112233", "Hola")
    assert resultado.estado == FALLIDO and not resultado.exito
    assert resultado.error == "Meta respondió 400 (código 131030)" and "5730011" not in (resultado.error or "")


def test_una_caida_de_red_es_fallido_sin_lanzar_excepcion() -> None:
    def cae(peticion: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("tiempo agotado", request=peticion)

    resultado = _cloud(cae)[0].enviar_texto("573001112233", "Hola")
    assert resultado.estado == FALLIDO and "ConnectTimeout" in (resultado.error or "")


def test_descarga_la_foto_en_dos_pasos_con_el_token_en_ambos() -> None:
    def meta(p: httpx.Request) -> httpx.Response:
        if str(p.url) == "https://graph.facebook.com/v21.0/MEDIA1":
            return httpx.Response(200, json={"url": "https://lookaside.fbsbx.com/foto"})
        return httpx.Response(200, content=b"bytes-de-la-foto")

    cliente, vistas = _cloud(meta)
    assert cliente.descargar_media("MEDIA1") == b"bytes-de-la-foto"
    assert [str(v.url) for v in vistas] == [
        "https://graph.facebook.com/v21.0/MEDIA1",
        "https://lookaside.fbsbx.com/foto",
    ]
    assert all(v.headers["Authorization"] == "Bearer token-secreto" for v in vistas)


@pytest.mark.parametrize(
    "respuestas",
    [
        [httpx.Response(404, json={})],  # el medio no existe o venció
        [httpx.Response(200, json={})],  # sin URL
        [httpx.Response(200, json={"url": "https://x/y"}), httpx.Response(500)],  # falla la descarga
    ],
)
def test_si_meta_no_entrega_la_foto_se_lanza_error_de_descarga(respuestas: list[httpx.Response]) -> None:
    cola = iter(respuestas)
    cliente, _ = _cloud(lambda p: next(cola))
    with pytest.raises(ErrorDescargaMedia):
        cliente.descargar_media("MEDIA1")


def test_una_caida_de_red_al_descargar_es_error_de_descarga() -> None:
    def cae(peticion: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lento", request=peticion)

    with pytest.raises(ErrorDescargaMedia, match="ReadTimeout"):
        _cloud(cae)[0].descargar_media("MEDIA1")
