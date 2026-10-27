import pytest

from app.core import config
from app.infrastructure import whatsapp_client as wa
from app.infrastructure.whatsapp_client import FALLIDO, SIMULADO, WhatsAppSimulado, get_whatsapp_client


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


def test_el_cliente_real_llega_en_la_fase_c(monkeypatch: pytest.MonkeyPatch) -> None:
    real = config.get_settings().model_copy(update={"integracion_whatsapp": "real"})
    monkeypatch.setattr(wa, "get_settings", lambda: real)
    with pytest.raises(RuntimeError, match="Fase C"):
        get_whatsapp_client()
