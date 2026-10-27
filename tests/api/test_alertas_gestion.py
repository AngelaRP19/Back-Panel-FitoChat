"""Confirmar, descartar, destinatarios, borrador, comunicar y corregir alertas (solo el líder técnico)."""

import asyncio
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.core import config
from app.infrastructure.whatsapp_client import WhatsAppSimulado
from app.main import app
from app.modules.panel_api.alertas import repository as repo_alertas
from app.modules.panel_api.alertas import service as servicio_alertas
from app.modules.panel_api.alertas.router import get_whatsapp
from tests.api.conftest import Mundo, auditoria_de, consultar
from tests.api.conftest_alertas import Zona, construir_zona

URL = "/api/v1/alertas"
MENSAJE = "Revise el envés de las hojas y avise al técnico si ve insectos"


@pytest.fixture
def whatsapp(api: TestClient) -> Iterator[WhatsAppSimulado]:
    cliente = WhatsAppSimulado()
    app.dependency_overrides[get_whatsapp] = lambda: cliente
    yield cliente


@pytest.fixture
def zona(api: TestClient, mundo: Mundo, migrated: Engine, whatsapp: WhatsAppSimulado) -> Zona:
    return construir_zona(api, mundo, migrated)


def celular_e164(productor: dict[str, Any]) -> str:
    return "57" + productor["celular"]


def poner_estado(motor: Engine, id_alerta: str, estado: str) -> None:
    with motor.begin() as c:
        c.execute(text("UPDATE alertas SET estado_alerta = :e WHERE id_alerta = :a"), {"e": estado, "a": id_alerta})


def envios(motor: Engine, id_alerta: str, tipo: str = "ALERTA") -> dict[str, str]:
    """`{celular del productor: estado del envío}`."""
    filas = consultar(
        motor,
        "SELECT p.telefono_productor AS t, d.estado_envio AS e FROM alerta_destinatarios d "
        "JOIN productores p USING (id_productor) WHERE d.id_alerta = :a AND d.tipo_envio = :t",
        a=id_alerta,
        t=tipo,
    )
    return {f["t"]: f["e"] for f in filas}


def comunicar(
    api: TestClient, mundo: Mundo, id_alerta: str, mensaje: str = MENSAJE, veredas: Any = None
) -> httpx.Response:
    cuerpo = {"mensaje": mensaje, "veredas": ["CENTRO", "SAN_LUIS"] if veredas is None else veredas}
    return api.post(f"{URL}/{id_alerta}/comunicar", json=cuerpo, headers=mundo.lider.headers)


def corregir(
    api: TestClient, mundo: Mundo, id_alerta: str, mensaje: str = "Fue un error de nuestro sistema"
) -> httpx.Response:
    return api.post(f"{URL}/{id_alerta}/corregir", json={"mensaje": mensaje}, headers=mundo.lider.headers)


def confirmada(api: TestClient, mundo: Mundo, zona: Zona) -> str:
    assert api.post(f"{URL}/{zona.alerta}/confirmar", headers=mundo.lider.headers).status_code == 200
    return zona.alerta


# --- confirmar y descartar ---------------------------------------------------------------------------------------


def test_confirmar_una_alerta_generada(api: TestClient, mundo: Mundo, zona: Zona, migrated: Engine) -> None:
    r = api.post(f"{URL}/{zona.alerta}/confirmar", headers=mundo.lider.headers)
    assert r.status_code == 200
    d = r.json()
    assert d["estado"] == "CONFIRMADA" and d["confirmada_por"] == {"id": mundo.lider.id, "nombre": "Laura Líder"}
    assert d["confirmada_en"] is not None
    fila = consultar(migrated, "SELECT * FROM alertas")[0]
    assert fila["estado_alerta"] == "CONFIRMADA" and fila["id_lider_gestion"] == mundo.lider.id


def test_confirmar_tambien_desde_en_revision(api: TestClient, mundo: Mundo, zona: Zona, migrated: Engine) -> None:
    poner_estado(migrated, zona.alerta, "EN_REVISION")
    assert api.post(f"{URL}/{zona.alerta}/confirmar", headers=mundo.lider.headers).json()["estado"] == "CONFIRMADA"


def test_confirmar_se_audita(api: TestClient, mundo: Mundo, zona: Zona, migrated: Engine) -> None:
    api.post(f"{URL}/{zona.alerta}/confirmar", headers=mundo.lider.headers)
    [registro] = [a for a in auditoria_de(migrated) if a["accion"] == "CONFIRMAR_ALERTA"]
    assert (registro["origen"], registro["id_lider_tecnico"], registro["entidad_afectada"]) == (
        "LIDER",
        mundo.lider.id,
        "alertas",
    )
    assert zona.alerta in registro["descripcion"]


def test_confirmar_dos_veces_409(api: TestClient, mundo: Mundo, zona: Zona, migrated: Engine) -> None:
    confirmada(api, mundo, zona)
    r = api.post(f"{URL}/{zona.alerta}/confirmar", headers=mundo.lider.headers)
    assert r.status_code == 409 and r.json()["codigo"] == "TRANSICION_INVALIDA"
    assert len([a for a in auditoria_de(migrated) if a["accion"] == "CONFIRMAR_ALERTA"]) == 1


def test_descartar_con_justificacion(api: TestClient, mundo: Mundo, zona: Zona, migrated: Engine) -> None:
    r = api.post(
        f"{URL}/{zona.alerta}/descartar", json={"justificacion": "  Son casos aislados  "}, headers=mundo.lider.headers
    )
    assert r.status_code == 200
    d = r.json()
    assert d["estado"] == "DESCARTADA" and d["motivo_descarte"] == "Son casos aislados"
    assert d["descarte"]["motivo"] == "Son casos aislados" and d["descarte"]["por"]["id"] == mundo.lider.id
    assert d["confirmada_por"] is None and d["confirmada_en"] is None
    [registro] = [a for a in auditoria_de(migrated) if a["accion"] == "DESCARTAR_ALERTA"]
    assert "Son casos aislados" in registro["valor_nuevo"]


@pytest.mark.parametrize("justificacion", ["", "   ", "x" * 501])
def test_el_descarte_exige_justificacion_valida(
    api: TestClient, mundo: Mundo, zona: Zona, migrated: Engine, justificacion: str
) -> None:
    r = api.post(f"{URL}/{zona.alerta}/descartar", json={"justificacion": justificacion}, headers=mundo.lider.headers)
    assert r.status_code == 422 and "justificacion" in r.json()["campos"]
    assert consultar(migrated, "SELECT estado_alerta AS e FROM alertas")[0]["e"] == "GENERADA"


def test_el_descarte_sin_cuerpo_422(api: TestClient, mundo: Mundo, zona: Zona) -> None:
    assert api.post(f"{URL}/{zona.alerta}/descartar", json={}, headers=mundo.lider.headers).status_code == 422


@pytest.mark.parametrize("estado", ["CONFIRMADA", "DESCARTADA", "COMUNICADA", "CORREGIDA"])
def test_solo_se_decide_una_alerta_abierta(
    api: TestClient, mundo: Mundo, zona: Zona, migrated: Engine, estado: str
) -> None:
    poner_estado(migrated, zona.alerta, estado)
    for r in (
        api.post(f"{URL}/{zona.alerta}/confirmar", headers=mundo.lider.headers),
        api.post(f"{URL}/{zona.alerta}/descartar", json={"justificacion": "x"}, headers=mundo.lider.headers),
    ):
        assert r.status_code == 409 and r.json()["codigo"] == "TRANSICION_INVALIDA"
    assert consultar(migrated, "SELECT estado_alerta AS e FROM alertas")[0]["e"] == estado


def test_confirmar_y_descartar_a_la_vez_gana_uno(api: TestClient, mundo: Mundo, zona: Zona, migrated: Engine) -> None:
    async def disparar() -> list[httpx.Response]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            return await asyncio.gather(
                c.post(f"{URL}/{zona.alerta}/confirmar", headers=mundo.lider.headers),
                c.post(f"{URL}/{zona.alerta}/descartar", json={"justificacion": "x"}, headers=mundo.lider.headers),
            )

    assert sorted(r.status_code for r in asyncio.run(disparar())) == [200, 409]
    assert consultar(migrated, "SELECT estado_alerta AS e FROM alertas")[0]["e"] in ("CONFIRMADA", "DESCARTADA")


@pytest.mark.parametrize("accion", ["confirmar", "descartar", "comunicar", "corregir", "destinatarios", "borrador"])
def test_alerta_inexistente_404(api: TestClient, mundo: Mundo, accion: str) -> None:
    cuerpo = {"justificacion": "x"} if accion == "descartar" else {"mensaje": MENSAJE, "veredas": ["CENTRO"]}
    metodo = api.get if accion == "destinatarios" else api.put if accion == "borrador" else api.post
    kwargs: dict[str, Any] = {"headers": mundo.lider.headers}
    if accion not in ("confirmar", "destinatarios"):
        kwargs["json"] = cuerpo
    assert metodo(f"{URL}/ALR-999999/{accion}", **kwargs).status_code == 404


# --- destinatarios -----------------------------------------------------------------------------------------------


def test_los_destinatarios_estan_dentro_del_radio_de_la_alerta(api: TestClient, mundo: Mundo, zona: Zona) -> None:
    r = api.get(f"{URL}/{zona.alerta}/destinatarios", headers=mundo.lider.headers)
    assert r.status_code == 200
    por_vereda = {v["vereda"]: v for v in r.json()}
    assert set(por_vereda) == {"CENTRO", "SAN_LUIS"}
    assert {p["nombre"] for p in por_vereda["CENTRO"]["productores"]} == {"Ana Pérez", "Carlos Cerca"}
    assert [p["nombre"] for p in por_vereda["SAN_LUIS"]["productores"]] == ["Álvaro Díaz"]  # «Luis Lejos» queda fuera
    assert "Luis Lejos" not in str(r.json())


def test_los_deshabilitados_se_cuentan_pero_no_reciben(api: TestClient, mundo: Mundo, zona: Zona) -> None:
    centro = next(
        v
        for v in api.get(f"{URL}/{zona.alerta}/destinatarios", headers=mundo.lider.headers).json()
        if v["vereda"] == "CENTRO"
    )
    assert centro["deshabilitados"] == 1
    assert "Diego Deshabilitado" not in str(centro["productores"])


def test_marca_las_veredas_donde_estan_los_casos(api: TestClient, mundo: Mundo, zona: Zona) -> None:
    lista = api.get(f"{URL}/{zona.alerta}/destinatarios", headers=mundo.lider.headers).json()
    assert {v["vereda"]: v["zona_alerta"] for v in lista} == {"CENTRO": True, "SAN_LUIS": False}
    assert lista[0]["vereda"] == "CENTRO"  # la vereda de los casos va primero


def test_los_celulares_salen_con_diez_digitos(api: TestClient, mundo: Mundo, zona: Zona) -> None:
    lista = api.get(f"{URL}/{zona.alerta}/destinatarios", headers=mundo.lider.headers).json()
    celulares = {p["celular"] for v in lista for p in v["productores"]}
    assert celulares == {zona.ana["celular"], zona.alvaro["celular"], zona.cerca["celular"]}


def test_filtra_por_vereda(api: TestClient, mundo: Mundo, zona: Zona) -> None:
    lista = api.get(
        f"{URL}/{zona.alerta}/destinatarios", params={"vereda": "SAN_LUIS"}, headers=mundo.lider.headers
    ).json()
    assert [v["vereda"] for v in lista] == ["SAN_LUIS"]
    assert (
        api.get(f"{URL}/{zona.alerta}/destinatarios", params={"vereda": "OTRA"}, headers=mundo.lider.headers).json()
        == []
    )


def test_cambiar_distancia_km_altera_los_destinatarios(
    api: TestClient, mundo: Mundo, zona: Zona, migrated: Engine
) -> None:
    """Criterio de aceptación. «Luis Lejos» está a ~6,7 km: con un radio de 7 km entra."""

    def nombres() -> set[str]:
        lista = api.get(f"{URL}/{zona.alerta}/destinatarios", headers=mundo.lider.headers).json()
        return {p["nombre"] for v in lista for p in v["productores"]}

    assert "Luis Lejos" not in nombres()
    with migrated.begin() as c:
        c.execute(text("UPDATE alertas SET distancia_km = 7 WHERE id_alerta = :a"), {"a": zona.alerta})
    assert "Luis Lejos" in nombres()
    with migrated.begin() as c:
        c.execute(text("UPDATE alertas SET distancia_km = 0.5 WHERE id_alerta = :a"), {"a": zona.alerta})
    assert nombres() == {"Ana Pérez", "Álvaro Díaz", "Carlos Cerca"}  # solo quienes tienen finca donde hay casos


def test_los_destinatarios_usan_el_radio_de_la_alerta_no_el_parametro_actual(
    api: TestClient, mundo: Mundo, zona: Zona
) -> None:
    api.put(
        f"{URL}/parametros/TRIPS",
        json={"num_casos": 3, "distancia_km": 50, "intervalo_dias": 30},
        headers=mundo.lider.headers,
    )
    lista = api.get(f"{URL}/{zona.alerta}/destinatarios", headers=mundo.lider.headers).json()
    assert "Luis Lejos" not in str(lista)


# --- borrador ----------------------------------------------------------------------------------------------------


def test_guarda_el_borrador_sin_enviarlo(api: TestClient, mundo: Mundo, zona: Zona, whatsapp: WhatsAppSimulado) -> None:
    confirmada(api, mundo, zona)
    r = api.put(f"{URL}/{zona.alerta}/borrador", json={"mensaje": f"  {MENSAJE}  "}, headers=mundo.lider.headers)
    assert r.status_code == 200 and r.json()["borrador"] == {"mensaje": MENSAJE} and r.json()["estado"] == "CONFIRMADA"
    assert whatsapp.enviados == []
    again = api.put(f"{URL}/{zona.alerta}/borrador", json={"mensaje": "Otro texto"}, headers=mundo.lider.headers)
    assert again.json()["borrador"]["mensaje"] == "Otro texto"


@pytest.mark.parametrize("estado", ["GENERADA", "DESCARTADA", "COMUNICADA", "CORREGIDA"])
def test_el_borrador_solo_con_la_alerta_confirmada(
    api: TestClient, mundo: Mundo, zona: Zona, migrated: Engine, estado: str
) -> None:
    poner_estado(migrated, zona.alerta, estado)
    r = api.put(f"{URL}/{zona.alerta}/borrador", json={"mensaje": MENSAJE}, headers=mundo.lider.headers)
    assert r.status_code == 409 and r.json()["codigo"] == "TRANSICION_INVALIDA"


@pytest.mark.parametrize(
    "mensaje",
    [
        "",
        "   ",
        "x" * 301,
        "Aplique 2 ml de insecticida por litro",
        "Fumigue hoy mismo",
        "Use un fungicida",
        "Haga un tratamiento",
    ],
)
def test_el_mensaje_valido_no_receta_ni_se_pasa_de_largo(
    api: TestClient, mundo: Mundo, zona: Zona, mensaje: str
) -> None:
    confirmada(api, mundo, zona)
    r = api.put(f"{URL}/{zona.alerta}/borrador", json={"mensaje": mensaje}, headers=mundo.lider.headers)
    assert r.status_code == 422 and "mensaje" in r.json()["campos"]


def test_el_limite_son_300_caracteres(api: TestClient, mundo: Mundo, zona: Zona) -> None:
    confirmada(api, mundo, zona)
    assert (
        api.put(f"{URL}/{zona.alerta}/borrador", json={"mensaje": "x" * 300}, headers=mundo.lider.headers).status_code
        == 200
    )


# --- comunicar ---------------------------------------------------------------------------------------------------


def test_comunicar_envia_a_los_activos_dentro_del_radio(
    api: TestClient, mundo: Mundo, zona: Zona, whatsapp: WhatsAppSimulado, migrated: Engine
) -> None:
    confirmada(api, mundo, zona)
    r = comunicar(api, mundo, zona.alerta)
    assert r.status_code == 200
    d = r.json()
    assert d["estado"] == "COMUNICADA" and d["productores_notificados"] == 3 and d["envios_fallidos"] == 0
    assert d["comunicada_en"] is not None and d["borrador"] is None
    assert {tel for tel, _ in whatsapp.enviados} == {celular_e164(p) for p in zona.activos_en_radio}
    assert celular_e164(zona.lejos) not in str(whatsapp.enviados) and celular_e164(zona.deshabilitado) not in str(
        whatsapp.enviados
    )
    assert d["mensaje_enviado"].startswith("Aviso fitosanitario ANUC Zetaquirá") and MENSAJE in d["mensaje_enviado"]
    assert "Trips" in whatsapp.enviados[0][1]


def test_en_modo_simulado_el_estado_es_simulado_nunca_enviado(
    api: TestClient, mundo: Mundo, zona: Zona, migrated: Engine
) -> None:
    confirmada(api, mundo, zona)
    comunicar(api, mundo, zona.alerta)
    assert set(envios(migrated, zona.alerta).values()) == {"SIMULADO"}
    fila = consultar(migrated, "SELECT * FROM alerta_destinatarios")[0]
    assert fila["tipo_envio"] == "ALERTA" and fila["fecha_envio"] is not None and fila["detalle_error"] is None


def test_el_registro_por_destinatario_y_la_auditoria_no_llevan_telefonos(
    api: TestClient, mundo: Mundo, zona: Zona, migrated: Engine
) -> None:
    confirmada(api, mundo, zona)
    comunicar(api, mundo, zona.alerta)
    [registro] = [a for a in auditoria_de(migrated) if a["accion"] == "COMUNICAR_ALERTA"]
    assert registro["id_lider_tecnico"] == mundo.lider.id and "3 entregados" in registro["descripcion"]
    assert "573001112" not in " ".join(map(str, registro.values()))
    fila = consultar(migrated, "SELECT fecha_comunicacion_alerta AS f, recomendacion FROM alertas")[0]
    assert fila["f"] is not None and fila["recomendacion"] == MENSAJE


def test_solo_las_veredas_elegidas(api: TestClient, mundo: Mundo, zona: Zona, whatsapp: WhatsAppSimulado) -> None:
    confirmada(api, mundo, zona)
    d = comunicar(api, mundo, zona.alerta, veredas=["san_luis"]).json()
    assert d["productores_notificados"] == 1
    assert [tel for tel, _ in whatsapp.enviados] == [celular_e164(zona.alvaro)]


@pytest.mark.parametrize("veredas", [[], ["OTRA_VEREDA"], [" "]])
def test_sin_destinatarios_422(
    api: TestClient, mundo: Mundo, zona: Zona, veredas: list[str], whatsapp: WhatsAppSimulado
) -> None:
    confirmada(api, mundo, zona)
    r = comunicar(api, mundo, zona.alerta, veredas=veredas)
    assert r.status_code == 422 and "veredas" in r.json()["campos"]
    assert (
        whatsapp.enviados == []
        and api.get(f"{URL}/{zona.alerta}", headers=mundo.lider.headers).json()["estado"] == "CONFIRMADA"
    )


@pytest.mark.parametrize("mensaje", ["", "x" * 301, "Aplique producto en las hojas"])
def test_comunicar_valida_el_mensaje(
    api: TestClient, mundo: Mundo, zona: Zona, mensaje: str, whatsapp: WhatsAppSimulado
) -> None:
    confirmada(api, mundo, zona)
    r = comunicar(api, mundo, zona.alerta, mensaje=mensaje)
    assert r.status_code == 422 and "mensaje" in r.json()["campos"] and whatsapp.enviados == []


@pytest.mark.parametrize("estado", ["GENERADA", "EN_REVISION", "DESCARTADA", "CORREGIDA"])
def test_solo_se_comunica_desde_confirmada(
    api: TestClient, mundo: Mundo, zona: Zona, migrated: Engine, estado: str, whatsapp: WhatsAppSimulado
) -> None:
    poner_estado(migrated, zona.alerta, estado)
    r = comunicar(api, mundo, zona.alerta)
    assert r.status_code == 409 and r.json()["codigo"] == "TRANSICION_INVALIDA" and whatsapp.enviados == []


def test_comunicar_dos_veces_409_sin_reenviar(
    api: TestClient, mundo: Mundo, zona: Zona, whatsapp: WhatsAppSimulado
) -> None:
    confirmada(api, mundo, zona)
    comunicar(api, mundo, zona.alerta)
    r = comunicar(api, mundo, zona.alerta)
    assert r.status_code == 409 and len(whatsapp.enviados) == 3


def test_un_envio_fallido_no_detiene_a_los_demas(
    api: TestClient, mundo: Mundo, zona: Zona, whatsapp: WhatsAppSimulado, migrated: Engine
) -> None:
    whatsapp.fallan.add(celular_e164(zona.cerca))
    confirmada(api, mundo, zona)
    d = comunicar(api, mundo, zona.alerta).json()
    assert d["estado"] == "COMUNICADA" and d["productores_notificados"] == 2 and d["envios_fallidos"] == 1
    estados = envios(migrated, zona.alerta)
    assert estados[celular_e164(zona.cerca)] == "FALLIDO" and list(estados.values()).count("SIMULADO") == 2
    assert consultar(migrated, "SELECT detalle_error FROM alerta_destinatarios WHERE estado_envio = 'FALLIDO'")[0][
        "detalle_error"
    ]


def test_un_cliente_que_lanza_excepcion_cuenta_como_fallido(
    api: TestClient, mundo: Mundo, zona: Zona, whatsapp: WhatsAppSimulado, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = whatsapp.enviar_texto

    def explota(telefono: str, texto: str) -> Any:
        if telefono == celular_e164(zona.ana):
            raise ConnectionError("sin red")
        return original(telefono, texto)

    monkeypatch.setattr(whatsapp, "enviar_texto", explota)
    confirmada(api, mundo, zona)
    d = comunicar(api, mundo, zona.alerta).json()
    assert d["estado"] == "COMUNICADA" and d["productores_notificados"] == 2 and d["envios_fallidos"] == 1


def test_reintento_seguro_solo_reenvia_los_fallidos(
    api: TestClient, mundo: Mundo, zona: Zona, whatsapp: WhatsAppSimulado, migrated: Engine
) -> None:
    whatsapp.fallan.add(celular_e164(zona.cerca))
    confirmada(api, mundo, zona)
    comunicar(api, mundo, zona.alerta)
    assert len(whatsapp.enviados) == 2
    whatsapp.fallan.clear()
    d = comunicar(api, mundo, zona.alerta, mensaje="Otro texto que se ignora en el reintento").json()
    assert d["productores_notificados"] == 3 and d["envios_fallidos"] == 0
    telefonos = [tel for tel, _ in whatsapp.enviados]
    assert len(telefonos) == 3 and len(set(telefonos)) == 3  # nadie recibió el mensaje dos veces
    assert "Otro texto" not in d["mensaje_enviado"]  # el reintento conserva el mensaje original
    assert set(envios(migrated, zona.alerta).values()) == {"SIMULADO"}


def test_una_comunicacion_interrumpida_se_reanuda_sin_duplicar(
    api: TestClient,
    mundo: Mundo,
    zona: Zona,
    whatsapp: WhatsAppSimulado,
    migrated: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El proceso se cae después de enviar el primero: la alerta sigue CONFIRMADA y el reintento sigue con el resto."""
    original = repo_alertas.registrar_resultado
    llamadas = {"n": 0}

    async def cae_en_la_segunda(*args: Any, **kwargs: Any) -> None:
        llamadas["n"] += 1
        if llamadas["n"] == 2:
            raise RuntimeError("el proceso murió")
        await original(*args, **kwargs)

    confirmada(api, mundo, zona)
    monkeypatch.setattr(repo_alertas, "registrar_resultado", cae_en_la_segunda)
    assert comunicar(api, mundo, zona.alerta).status_code == 500
    assert api.get(f"{URL}/{zona.alerta}", headers=mundo.lider.headers).json()["estado"] == "CONFIRMADA"
    monkeypatch.setattr(repo_alertas, "registrar_resultado", original)

    d = comunicar(api, mundo, zona.alerta).json()
    # El envío que quedó «en curso» no se repite hasta que pase el tiempo de espera: la alerta aún no se cierra.
    assert d["estado"] == "CONFIRMADA"
    pendientes = [t for t, e in envios(migrated, zona.alerta).items() if e == "ENVIANDO"]
    assert len(pendientes) == 1
    with migrated.begin() as c:
        c.execute(
            text(
                "UPDATE alerta_destinatarios SET fecha_envio = now() - interval '11 minutes' WHERE estado_envio = 'ENVIANDO'"
            )
        )
    d = comunicar(api, mundo, zona.alerta).json()
    assert d["estado"] == "COMUNICADA" and d["productores_notificados"] == 3
    assert set(envios(migrated, zona.alerta).values()) == {"SIMULADO"}


def test_tres_comunicaciones_simultaneas_envian_una_sola_vez_a_cada_uno(
    api: TestClient, mundo: Mundo, zona: Zona, whatsapp: WhatsAppSimulado, migrated: Engine
) -> None:
    confirmada(api, mundo, zona)

    async def disparar() -> list[httpx.Response]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            cuerpo = {"mensaje": MENSAJE, "veredas": ["CENTRO", "SAN_LUIS"]}
            return await asyncio.gather(
                *(c.post(f"{URL}/{zona.alerta}/comunicar", json=cuerpo, headers=mundo.lider.headers) for _ in range(3))
            )

    respuestas = asyncio.run(disparar())
    assert {r.status_code for r in respuestas} <= {200, 409} and any(r.status_code == 200 for r in respuestas)
    telefonos = [tel for tel, _ in whatsapp.enviados]
    assert len(telefonos) == len(set(telefonos)) == 3
    assert consultar(migrated, "SELECT estado_alerta AS e FROM alertas")[0]["e"] == "COMUNICADA"


def test_envia_en_lotes_con_pausa_entre_lotes(
    api: TestClient, mundo: Mundo, zona: Zona, whatsapp: WhatsAppSimulado, monkeypatch: pytest.MonkeyPatch
) -> None:
    ajustes = config.get_settings().model_copy(update={"alertas_lote_envio": 2, "alertas_pausa_lote_segundos": 0.5})
    monkeypatch.setattr(servicio_alertas, "get_settings", lambda: ajustes)
    pausas: list[float] = []

    async def pausa(segundos: float) -> None:
        pausas.append(segundos)

    monkeypatch.setattr(servicio_alertas.asyncio, "sleep", pausa)
    confirmada(api, mundo, zona)
    d = comunicar(api, mundo, zona.alerta).json()
    assert d["productores_notificados"] == 3 and pausas == [0.5]  # lotes de 2 y de 1: una pausa tras el lote lleno


def test_sin_cliente_de_whatsapp_responde_503_y_no_cambia_nada(
    api: TestClient, mundo: Mundo, zona: Zona, migrated: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.infrastructure import whatsapp_client

    app.dependency_overrides.pop(get_whatsapp, None)
    confirmada(api, mundo, zona)
    real = config.get_settings().model_copy(update={"integracion_whatsapp": "real"})
    monkeypatch.setattr(whatsapp_client, "get_settings", lambda: real)
    r = comunicar(api, mundo, zona.alerta)
    assert r.status_code == 503 and r.json()["codigo"] == "ERROR_INTERNO"
    assert consultar(migrated, "SELECT estado_alerta AS e FROM alertas")[0]["e"] == "CONFIRMADA"


# --- corregir (D-17) ---------------------------------------------------------------------------------------------


@pytest.fixture
def comunicada(api: TestClient, mundo: Mundo, zona: Zona, whatsapp: WhatsAppSimulado) -> Zona:
    confirmada(api, mundo, zona)
    assert comunicar(api, mundo, zona.alerta).json()["estado"] == "COMUNICADA"
    whatsapp.enviados.clear()
    return zona


def test_corregir_envia_la_plantilla_a_quienes_recibieron_la_alerta(
    api: TestClient, mundo: Mundo, comunicada: Zona, whatsapp: WhatsAppSimulado, migrated: Engine
) -> None:
    r = corregir(api, mundo, comunicada.alerta, "Fue un error de nuestro sistema")
    assert r.status_code == 200
    d = r.json()
    assert d["estado"] == "CORREGIDA"
    assert (
        d["correccion"]["mensaje"] == "Fue un error de nuestro sistema"
        and d["correccion"]["por"]["id"] == mundo.lider.id
    )
    assert {tel for tel, _ in whatsapp.enviados} == {celular_e164(p) for p in comunicada.activos_en_radio}
    texto = whatsapp.enviados[0][1]
    assert (
        "no aplica" in texto
        and "haga caso omiso" in texto
        and "Trips" in texto
        and "Fue un error de nuestro sistema" in texto
    )
    assert set(envios(migrated, comunicada.alerta, "CORRECCION").values()) == {"SIMULADO"}
    assert set(envios(migrated, comunicada.alerta, "ALERTA").values()) == {
        "SIMULADO"
    }  # los envíos originales se conservan
    [registro] = [a for a in auditoria_de(migrated) if a["accion"] == "CORREGIR_ALERTA"]
    assert registro["id_lider_tecnico"] == mundo.lider.id
    fila = consultar(migrated, "SELECT * FROM alertas")[0]
    assert fila["id_lider_correccion"] == mundo.lider.id and fila["fecha_correccion"] is not None


def test_corregir_dos_veces_409(api: TestClient, mundo: Mundo, comunicada: Zona, whatsapp: WhatsAppSimulado) -> None:
    """Criterio de aceptación: un segundo intento → 409, y nadie recibe la corrección dos veces."""
    corregir(api, mundo, comunicada.alerta)
    r = corregir(api, mundo, comunicada.alerta)
    assert r.status_code == 409 and r.json()["codigo"] == "TRANSICION_INVALIDA"
    assert len(whatsapp.enviados) == 3


def test_solo_recibe_la_correccion_quien_recibio_la_alerta(
    api: TestClient, mundo: Mundo, zona: Zona, whatsapp: WhatsAppSimulado, migrated: Engine
) -> None:
    whatsapp.fallan.add(celular_e164(zona.cerca))  # a este nunca le llegó la alerta
    confirmada(api, mundo, zona)
    comunicar(api, mundo, zona.alerta)
    whatsapp.fallan.clear()
    whatsapp.enviados.clear()
    corregir(api, mundo, zona.alerta)
    assert {tel for tel, _ in whatsapp.enviados} == {celular_e164(zona.ana), celular_e164(zona.alvaro)}
    assert celular_e164(zona.cerca) not in envios(migrated, zona.alerta, "CORRECCION")


@pytest.mark.parametrize("estado", ["GENERADA", "EN_REVISION", "CONFIRMADA", "DESCARTADA"])
def test_solo_se_corrige_una_alerta_comunicada(
    api: TestClient, mundo: Mundo, zona: Zona, migrated: Engine, estado: str, whatsapp: WhatsAppSimulado
) -> None:
    poner_estado(migrated, zona.alerta, estado)
    r = corregir(api, mundo, zona.alerta)
    assert r.status_code == 409 and r.json()["codigo"] == "TRANSICION_INVALIDA" and whatsapp.enviados == []


@pytest.mark.parametrize("mensaje", ["", "   ", "x" * 301, "Aplique un fungicida", "La dosis fue un error"])
def test_la_explicacion_se_valida_como_cualquier_mensaje(
    api: TestClient, mundo: Mundo, comunicada: Zona, whatsapp: WhatsAppSimulado, mensaje: str
) -> None:
    r = corregir(api, mundo, comunicada.alerta, mensaje)
    assert r.status_code == 422 and "mensaje" in r.json()["campos"] and whatsapp.enviados == []


def test_una_alerta_que_nadie_recibio_no_se_corrige(
    api: TestClient, mundo: Mundo, zona: Zona, whatsapp: WhatsAppSimulado, migrated: Engine
) -> None:
    confirmada(api, mundo, zona)
    for p in zona.activos_en_radio:
        whatsapp.fallan.add(celular_e164(p))
    d = comunicar(api, mundo, zona.alerta).json()
    assert d["estado"] == "COMUNICADA" and d["productores_notificados"] == 0
    r = corregir(api, mundo, zona.alerta)
    assert r.status_code == 409 and "Nadie recibió" in r.json()["mensaje"]


def test_una_correccion_interrumpida_se_reanuda_con_la_explicacion_original(
    api: TestClient,
    mundo: Mundo,
    comunicada: Zona,
    whatsapp: WhatsAppSimulado,
    migrated: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = repo_alertas.registrar_resultado
    llamadas = {"n": 0}

    async def cae_en_la_segunda(*args: Any, **kwargs: Any) -> None:
        llamadas["n"] += 1
        if llamadas["n"] == 2:
            raise RuntimeError("el proceso murió")
        await original(*args, **kwargs)

    monkeypatch.setattr(repo_alertas, "registrar_resultado", cae_en_la_segunda)
    assert corregir(api, mundo, comunicada.alerta, "Primera explicación").status_code == 500
    monkeypatch.setattr(repo_alertas, "registrar_resultado", original)
    assert api.get(f"{URL}/{comunicada.alerta}", headers=mundo.lider.headers).json()["estado"] == "COMUNICADA"

    with migrated.begin() as c:
        c.execute(
            text(
                "UPDATE alerta_destinatarios SET fecha_envio = now() - interval '11 minutes' WHERE estado_envio = 'ENVIANDO'"
            )
        )
    d = corregir(api, mundo, comunicada.alerta, "Segunda explicación distinta").json()
    assert d["estado"] == "CORREGIDA" and d["correccion"]["mensaje"] == "Primera explicación"
    assert set(envios(migrated, comunicada.alerta, "CORRECCION").values()) == {"SIMULADO"}
    assert all("Primera explicación" in texto for _, texto in whatsapp.enviados)


def test_dos_correcciones_simultaneas_envian_una_sola_vez(
    api: TestClient, mundo: Mundo, comunicada: Zona, whatsapp: WhatsAppSimulado
) -> None:
    async def disparar() -> list[httpx.Response]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            return await asyncio.gather(
                *(
                    c.post(
                        f"{URL}/{comunicada.alerta}/corregir", json={"mensaje": "Un error"}, headers=mundo.lider.headers
                    )
                    for _ in range(3)
                )
            )

    respuestas = asyncio.run(disparar())
    assert any(r.status_code == 200 for r in respuestas) and {r.status_code for r in respuestas} <= {200, 409}
    telefonos = [tel for tel, _ in whatsapp.enviados]
    assert len(telefonos) == len(set(telefonos)) == 3
