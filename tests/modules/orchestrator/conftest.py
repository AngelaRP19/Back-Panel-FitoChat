"""Fixtures del orquestador: una conversación por WhatsApp simulada contra la base real."""

from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.infrastructure.ai_client import AiSimulado, Turno
from app.infrastructure.conversaciones_store import ConversacionesEnMemoria
from app.infrastructure.db.seed import seed
from app.infrastructure.whatsapp_client import WhatsAppSimulado
from app.modules.consumer.procesador import Procesador
from app.modules.orchestrator.orquestador import Orquestador
from app.modules.orchestrator.puertos import (
    Identificacion,
    IdentificadorSimulado,
    PipelineSimulado,
)
from tests.api.conftest import vaciar

TELEFONO = "573001112233"


@dataclass
class Sistema:
    """Todo lo necesario para conversar: el orquestador conectado a un consumidor y a dobles de los servicios externos."""

    motor: Engine
    store: ConversacionesEnMemoria
    whatsapp: WhatsAppSimulado
    ai: AiSimulado
    pipeline: PipelineSimulado
    orquestador: Orquestador
    procesador: Procesador
    s3: "S3Registrador"
    identificador: "IdentificadorProgramable"
    _n: int = field(default=0)

    def escribir(self, texto: str, telefono: str = TELEFONO) -> list[str]:
        """Manda un mensaje de texto y devuelve lo que el bot respondió."""
        return self._enviar(telefono, "text", {"text": texto})

    def foto(self, media_id: str = "media-1", descripcion: str = "", telefono: str = TELEFONO) -> list[str]:
        contenido: dict[str, Any] = {"media_id": media_id, "mime_type": "image/jpeg"}
        if descripcion:
            contenido["caption"] = descripcion
        return self._enviar(telefono, "image", contenido)

    def _enviar(self, telefono: str, tipo: str, contenido: dict[str, Any]) -> list[str]:
        import json

        self._n += 1
        antes = len(self.whatsapp.enviados)
        cuerpo = json.dumps(
            {"wamid": f"wamid.{self._n}", "from": telefono, "type": tipo, "timestamp": "1", "content": contenido}
        )
        self.procesador.procesar(cuerpo)
        return [texto for _, texto in self.whatsapp.enviados[antes:]]

    def paso(self, telefono: str = TELEFONO) -> str | None:
        conv = self.store.obtener(telefono)
        return conv.paso if conv else None

    def consulta(self, sql: str, **parametros: Any) -> list[dict[str, Any]]:
        with self.motor.connect() as c:
            return [dict(f) for f in c.execute(text(sql), parametros).mappings()]

    def estado_caso(self) -> str | None:
        filas = self.consulta("SELECT e.nombre_estado AS n FROM casos c JOIN estados e USING (id_estado)")
        return str(filas[0]["n"]) if filas else None


class S3Registrador:
    """Doble de S3: anota qué claves se pidió borrar (con todas sus versiones)."""

    def __init__(self) -> None:
        self.borradas: list[str] = []

    def eliminar_todas_las_versiones(self, clave: str) -> int:
        self.borradas.append(clave)
        return 1


class IdentificadorProgramable(IdentificadorSimulado):
    def __init__(self) -> None:
        self.resultado: Identificacion | None = None

    def identificar(self, id_caso: str) -> Identificacion | None:
        return self.resultado


@pytest.fixture
def sistema(migrated: Engine) -> Iterator[Sistema]:
    vaciar(migrated)
    with migrated.begin() as conexion:
        seed(conexion)
    store, whatsapp, ai, pipeline = ConversacionesEnMemoria(), WhatsAppSimulado(), AiSimulado(), PipelineSimulado()
    s3, identificador = S3Registrador(), IdentificadorProgramable()
    orq = Orquestador(store, whatsapp, ai, pipeline, identificador, s3)  # type: ignore[arg-type]
    yield Sistema(
        migrated, store, whatsapp, ai, pipeline, orq, Procesador(migrated, orq.manejar, orq.aplicar), s3, identificador
    )
    vaciar(migrated)


def productor_con_finca(
    motor: Engine,
    *,
    consentimiento: bool = True,
    activo: bool = True,
    fincas: int = 1,
    cultivos: tuple[tuple[str, tuple[str, ...]], ...] = (("Lote 1", ("Gulupa",)),),
    telefono: str = TELEFONO,
) -> dict[str, Any]:
    """Productor ya registrado (por el panel) con sus fincas y, en cada una, los cultivos indicados."""
    with motor.begin() as c:
        c.execute(
            text(
                "INSERT INTO productores (id_productor, nombre_productor, telefono_productor, estado_cuenta_productor, "
                "fecha_registro_productor, fecha_consentimiento) VALUES ('PRO-000001', 'Ana Pérez', :t, :e, '2026-01-05', "
                + ("now()" if consentimiento else "NULL")
                + ")"
            ),
            {"t": telefono, "e": "ACTIVO" if activo else "DESHABILITADO"},
        )
        for n in range(1, fincas + 1):
            c.execute(
                text(
                    "INSERT INTO fincas (id_finca, nombre_finca, vereda_sector_finca, latitud_finca, longitud_finca, "
                    "id_productor) VALUES (:i, :n, 'Centro', 5.25, -73.17, 'PRO-000001')"
                ),
                {"i": f"FIN-00000{n}", "n": f"Finca {n}" if fincas > 1 else "La Esperanza"},
            )
            if n == 1:
                for k, (nombre, especies) in enumerate(cultivos, start=1):
                    id_cultivo = f"CUL-00000{k}"
                    c.execute(
                        text(
                            "INSERT INTO cultivos (id_cultivo, nombre_cultivo, latitud_cultivo, longitud_cultivo, id_finca, "
                            "tipo_cultivo) VALUES (:i, :n, 5.25, -73.17, 'FIN-000001', 'CAMPO_ABIERTO')"
                        ),
                        {"i": id_cultivo, "n": nombre},
                    )
                    for e in especies:
                        c.execute(
                            text(
                                "INSERT INTO cultivo_especie (id_cultivo, id_especie) "
                                "SELECT :i, id_especie FROM especies WHERE nombre_especie = :e"
                            ),
                            {"i": id_cultivo, "e": e},
                        )
    return {"id_productor": "PRO-000001"}


def programar(sistema: Sistema, *turnos: Turno) -> None:
    sistema.ai.turnos = deque(turnos)


def avanzar_inactividad(motor: Engine, horas: float) -> None:
    """Deja la última actividad de los casos `horas` atrás (para simular que el productor desapareció)."""
    with motor.begin() as c:
        c.execute(text("UPDATE casos SET ultima_actividad = now() - make_interval(secs => :s)"), {"s": horas * 3600})
