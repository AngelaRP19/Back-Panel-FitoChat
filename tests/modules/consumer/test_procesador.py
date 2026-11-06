"""Procesamiento idempotente de un mensaje: registro por wamid, manejador y deshacer si algo falla."""

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.modules.consumer.procesador import (
    MensajeEntrante,
    MensajeInvalido,
    Procesador,
    ResultadoProceso,
    interpretar,
    manejador_pendiente,
)
from tests.workers.conftest import filas


def cuerpo(wamid: str = "wamid.A1", telefono: str = "573001112233", **extra: Any) -> str:
    return json.dumps(
        {
            "wamid": wamid,
            "from": telefono,
            "type": "text",
            "timestamp": "1760000000",
            "content": {"text": "hola"},
            **extra,
        }
    )


class Recolector:
    """Manejador de prueba: anota cada mensaje que recibe."""

    def __init__(self) -> None:
        self.recibidos: list[MensajeEntrante] = []

    def __call__(self, sesion: Session, mensaje: MensajeEntrante) -> None:
        self.recibidos.append(mensaje)


# --- interpretar -------------------------------------------------------------------------------------------------


def test_interpreta_lo_que_encola_el_webhook() -> None:
    assert interpretar(cuerpo()) == ("wamid.A1", "573001112233", "text", "1760000000", {"text": "hola"})


def test_sin_marca_de_tiempo_ni_contenido() -> None:
    datos = interpretar(json.dumps({"wamid": "w", "from": "57300", "type": "image"}))
    assert datos == ("w", "57300", "image", None, {})


@pytest.mark.parametrize(
    "texto",
    [
        "no es json",
        "",
        "[1, 2]",
        "42",
        json.dumps({"from": "57", "type": "text"}),
        json.dumps({"wamid": "", "from": "57", "type": "text"}),
        json.dumps({"wamid": "w", "type": "text"}),
        json.dumps({"wamid": "w", "from": "57"}),
        json.dumps({"wamid": 5, "from": "57", "type": "text"}),
        json.dumps({"wamid": "w", "from": "57", "type": "text", "content": "texto"}),
    ],
)
def test_un_cuerpo_invalido_se_rechaza(texto: str) -> None:
    with pytest.raises(MensajeInvalido):
        interpretar(texto)


# --- procesar ----------------------------------------------------------------------------------------------------


def test_un_mensaje_nuevo_se_registra_y_llega_al_manejador(bd: Engine) -> None:
    manejador = Recolector()
    assert Procesador(bd, manejador).procesar(cuerpo()) is ResultadoProceso.PROCESADO
    [m] = manejador.recibidos
    assert (m.wamid, m.telefono, m.tipo, m.timestamp, m.contenido, m.id_productor) == (
        "wamid.A1", "573001112233", "text", "1760000000", {"text": "hola"}, None,
    )  # fmt: skip
    [fila] = filas(bd, "SELECT * FROM mensajes_whatsapp")
    assert fila["wamid"] == "wamid.A1" and fila["estado_procesamiento"] == "PROCESADO"
    assert fila["contenido"] == {"text": "hola"} and fila["id_mensaje"].startswith("MSG-") and fila["fecha_recepcion"]


def test_un_mensaje_repetido_no_se_procesa_dos_veces(bd: Engine) -> None:
    """Criterio de aceptación: un mensaje duplicado de WhatsApp no genera dos respuestas."""
    manejador = Recolector()
    procesador = Procesador(bd, manejador)
    resultados = [procesador.procesar(cuerpo()) for _ in range(3)]
    assert resultados == [ResultadoProceso.PROCESADO, ResultadoProceso.DUPLICADO, ResultadoProceso.DUPLICADO]
    assert len(manejador.recibidos) == 1 and len(filas(bd, "SELECT 1 FROM mensajes_whatsapp")) == 1


def test_mensajes_distintos_se_procesan_los_dos(bd: Engine) -> None:
    manejador = Recolector()
    procesador = Procesador(bd, manejador)
    procesador.procesar(cuerpo("wamid.A1"))
    procesador.procesar(cuerpo("wamid.A2"))
    assert [m.wamid for m in manejador.recibidos] == ["wamid.A1", "wamid.A2"]


def test_se_enlaza_al_productor_si_el_numero_ya_esta_registrado(bd: Engine) -> None:
    with bd.begin() as c:
        c.execute(
            text(
                "INSERT INTO productores (id_productor, nombre_productor, telefono_productor, estado_cuenta_productor, "
                "fecha_registro_productor) VALUES ('PRO-000001', 'Ana', '573001112233', 'ACTIVO', '2026-01-05')"
            )
        )
    manejador = Recolector()
    Procesador(bd, manejador).procesar(cuerpo())
    Procesador(bd, manejador).procesar(cuerpo("wamid.A2", telefono="573009998877"))
    assert [m.id_productor for m in manejador.recibidos] == ["PRO-000001", None]
    assert [f["id_productor"] for f in filas(bd, "SELECT id_productor FROM mensajes_whatsapp ORDER BY wamid")] == [
        "PRO-000001",
        None,
    ]


def test_si_el_manejador_falla_no_queda_nada_y_el_reintento_lo_procesa(bd: Engine) -> None:
    """Registro del mensaje y efectos del manejador van en una sola transacción: se deshacen juntos."""
    intentos: list[int] = []

    def inestable(sesion: Session, mensaje: MensajeEntrante) -> None:
        sesion.execute(text("INSERT INTO preguntas (id_pregunta, texto_pregunta) VALUES ('PRE-000001', 'efecto')"))
        intentos.append(1)
        if len(intentos) == 1:
            raise RuntimeError("el orquestador falló")

    procesador = Procesador(bd, inestable)
    with pytest.raises(RuntimeError):
        procesador.procesar(cuerpo())
    assert filas(bd, "SELECT 1 FROM mensajes_whatsapp") == [] and filas(bd, "SELECT 1 FROM preguntas") == []
    assert procesador.procesar(cuerpo()) is ResultadoProceso.PROCESADO  # el reintento sí lo hace completo
    assert len(filas(bd, "SELECT 1 FROM mensajes_whatsapp")) == 1 and len(filas(bd, "SELECT 1 FROM preguntas")) == 1


def test_un_mensaje_invalido_no_deja_rastro(bd: Engine) -> None:
    manejador = Recolector()
    with pytest.raises(MensajeInvalido):
        Procesador(bd, manejador).procesar("no es json")
    assert manejador.recibidos == [] and filas(bd, "SELECT 1 FROM mensajes_whatsapp") == []


def test_el_mismo_wamid_a_la_vez_desde_varios_consumidores_se_procesa_una_vez(bd: Engine) -> None:
    llamadas: list[str] = []
    cerrojo = threading.Lock()

    def lento(sesion: Session, mensaje: MensajeEntrante) -> None:
        time.sleep(0.4)  # ensancha la ventana en la que los demás llegan mientras éste trabaja
        with cerrojo:
            llamadas.append(mensaje.wamid)

    procesador = Procesador(bd, lento)
    with ThreadPoolExecutor(max_workers=4) as pool:
        resultados = list(pool.map(lambda _: procesador.procesar(cuerpo()), range(4)))
    assert sorted(r.value for r in resultados) == ["DUPLICADO", "DUPLICADO", "DUPLICADO", "PROCESADO"]
    assert llamadas == ["wamid.A1"] and len(filas(bd, "SELECT 1 FROM mensajes_whatsapp")) == 1


def test_el_manejador_provisional_solo_deja_constancia(bd: Engine, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        assert Procesador(bd).procesar(cuerpo()) is ResultadoProceso.PROCESADO
    assert "orquestador aún no está conectado" in caplog.text
    assert "573001112233" not in caplog.text  # el teléfono sale enmascarado en los logs
    assert manejador_pendiente.__name__ == "manejador_pendiente"
