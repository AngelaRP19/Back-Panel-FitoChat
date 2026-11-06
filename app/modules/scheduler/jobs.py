"""Jobs programados (guía §11, módulo 8). EventBridge Scheduler los lanza como tareas ECS con `python -m app.jobs.run`.

Reglas comunes:
- **Idempotentes**: repetir un job no repite sus efectos (lo ya avisado, borrado o evaluado no se vuelve a tocar).
- **Con bloqueo**: dos ejecuciones del mismo job nunca corren a la vez (`pg_try_advisory_lock`); la segunda se omite.
- **Aislados por caso**: cada caso va en su propia transacción; el fallo de uno no frena a los demás, se cuenta como
  error y se reintenta en la siguiente ejecución.
- **Reloj inyectable**: todo se mide contra `ahora`, no contra el reloj de la base, para poder probar 48 h, 72 h y los
  6 meses con precisión de un minuto.
- Si borrar en S3 falla, **no se borra la fila** (ver `borrado.py`).
"""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.catalogos_api import ESTADOS_CASO, EstadoCaso
from app.core.state_machine import validar_transicion
from app.infrastructure.db import procedimientos as sp
from app.infrastructure.s3_storage_client import S3StorageClient
from app.infrastructure.whatsapp_client import WhatsAppClient
from app.modules.panel_api.notificaciones.service import (
    ALERTA_GENERADA,
    LIDERES_ACTIVOS_SQL,
    PROXIMOS_A_VENCER,
    Aviso,
    publicar_sync,
)
from app.modules.scheduler.borrado import borrar_caso

logger = logging.getLogger(__name__)

HORAS_ADVERTENCIA = 48
HORAS_DESCARTE = 72
EN_MANOS_DEL_CHATBOT = (ESTADOS_CASO.a_nombre(EstadoCaso.CREADO), ESTADOS_CASO.a_nombre(EstadoCaso.EN_ANALISIS))

# Texto PROVISIONAL del aviso de 48 h: el equipo debe validarlo. No receta nada.
MENSAJE_ADVERTENCIA = (
    "Hola, tu consulta fitosanitaria sigue abierta. Si no la continúas en las próximas 24 horas, "
    "se cancelará y se borrarán los datos que enviaste. Escríbenos para seguir."
)

_CASOS = "SELECT c.id_caso FROM casos c JOIN estados e ON e.id_estado = c.id_estado"
ACCION_DESCARTE = "BORRAR_INACTIVO"
ACCION_RETENCION = "BORRAR_RETENCION"


@dataclass
class Contexto:
    """Lo que un job necesita del exterior. `ahora` es el reloj contra el que se mide todo."""

    ahora: datetime
    s3: S3StorageClient | None = None
    whatsapp: WhatsAppClient | None = None


@dataclass
class ResultadoJob:
    nombre: str
    procesados: int = 0
    exitosos: int = 0
    omitidos: int = 0
    errores: int = 0
    ya_en_ejecucion: bool = False
    detalle: dict[str, Any] = field(default_factory=dict)

    @property
    def exito(self) -> bool:
        return self.errores == 0


@contextmanager
def bloqueo(engine: Engine, nombre: str) -> Iterator[bool]:
    """Candado de sesión por job: True si lo obtuvo; False si otra ejecución ya lo tiene."""
    clave = f"anuc.job.{nombre}"
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conexion:
        obtenido = bool(conexion.scalar(text("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": clave}))
        try:
            yield obtenido
        finally:
            if obtenido:
                conexion.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": clave})


def _ids(engine: Engine, sql: str, **parametros: Any) -> list[Any]:
    with Session(engine) as sesion:
        return [dict(f) for f in sesion.execute(text(sql), parametros).mappings()]


# --- advertencia de inactividad (48 h) -----------------------------------------------------------------------------


def advertencia_inactividad(engine: Engine, ctx: Contexto) -> ResultadoJob:
    """Casos del chatbot con ≥ 48 h sin actividad y sin advertencia: avisa por WhatsApp y la marca como enviada.

    Solo mira los que aún no cumplen las 72 h (esos los descarta el otro job). Si el productor está deshabilitado no
    se le escribe, pero la advertencia se marca para no reintentarla cada 15 minutos. Si el envío falla, no se marca:
    el siguiente ciclo lo reintenta.
    """
    assert ctx.whatsapp is not None, "advertencia_inactividad necesita el cliente de WhatsApp"
    resultado = ResultadoJob("advertencia_inactividad")
    candidatos = _ids(
        engine,
        """
        SELECT c.id_caso, p.telefono_productor AS telefono, p.estado_cuenta_productor = 'ACTIVO' AS activo
        FROM casos c
        JOIN estados e ON e.id_estado = c.id_estado
        JOIN cultivos cu ON cu.id_cultivo = c.id_cultivo
        JOIN fincas f ON f.id_finca = cu.id_finca
        JOIN productores p ON p.id_productor = f.id_productor
        WHERE e.nombre_estado = ANY(:estados) AND c.advertencia_inactividad_enviada = 'N'
          AND c.ultima_actividad <= CAST(:ahora AS timestamptz) - make_interval(hours => :aviso)
          AND c.ultima_actividad > CAST(:ahora AS timestamptz) - make_interval(hours => :descarte)
        ORDER BY c.id_caso
        """,
        estados=list(EN_MANOS_DEL_CHATBOT),
        ahora=ctx.ahora,
        aviso=HORAS_ADVERTENCIA,
        descarte=HORAS_DESCARTE,
    )
    for caso in candidatos:
        resultado.procesados += 1
        try:
            if caso["activo"]:
                envio = ctx.whatsapp.enviar_texto(caso["telefono"], MENSAJE_ADVERTENCIA)
                if not envio.exito:
                    resultado.errores += 1
                    logger.warning("No se pudo enviar la advertencia", extra={"id_caso": caso["id_caso"]})
                    continue
            else:
                resultado.omitidos += 1
            with Session(engine) as sesion, sesion.begin():
                sesion.execute(sp.MARCAR_ADVERTENCIA_ENVIADA, {"id_caso": caso["id_caso"]})
            resultado.exitosos += 1
        except DBAPIError as error:
            if sp.error_de_negocio(error) is sp.SqlState.TRANSICION_INVALIDA:  # el caso salió del chatbot entretanto
                resultado.omitidos += 1
                continue
            resultado.errores += 1
            logger.exception("Falló la advertencia", extra={"id_caso": caso["id_caso"]})
        except Exception:
            resultado.errores += 1
            logger.exception("Falló la advertencia", extra={"id_caso": caso["id_caso"]})
    return resultado


# --- borrado de casos: descarte (72 h) y retención (6 meses) ----------------------------------------------------------


def _borrar_vigentes(
    engine: Engine, ctx: Contexto, nombre: str, candidatos_sql: str, condicion_sql: str, accion: str, motivo: str
) -> ResultadoJob:
    """Borra los casos que cumplen la condición, uno por transacción.

    La condición se vuelve a comprobar con la fila bloqueada: si el productor escribió justo antes, el caso ya no
    cumple y no se borra.
    """
    assert ctx.s3 is not None, f"{nombre} necesita el cliente de S3"
    resultado = ResultadoJob(nombre)
    parametros = {
        "ahora": ctx.ahora,
        "estados": list(EN_MANOS_DEL_CHATBOT),
        "cerrado": ESTADOS_CASO.a_nombre(EstadoCaso.CERRADO),
        "horas": HORAS_DESCARTE,
    }
    ids = [f["id_caso"] for f in _ids(engine, candidatos_sql, **parametros)]
    fotos = versiones = 0
    for id_caso in ids:
        resultado.procesados += 1
        try:
            with Session(engine) as sesion, sesion.begin():
                vigente = sesion.scalar(
                    text(f"{_CASOS} WHERE c.id_caso = :c AND {condicion_sql} FOR UPDATE OF c"),
                    {**parametros, "c": id_caso},
                )
                if vigente is None:
                    resultado.omitidos += 1
                    continue
                if accion == ACCION_DESCARTE:
                    # Creado y En análisis comparten la regla hacia Descartado.
                    validar_transicion(EstadoCaso.EN_ANALISIS, EstadoCaso.DESCARTADO)
                borrado = borrar_caso(sesion, ctx.s3, id_caso, ctx.ahora, accion, motivo)
            fotos += borrado.fotos
            versiones += borrado.versiones_s3
            resultado.exitosos += 1
        except Exception:  # S3 u otra falla: la transacción se deshizo y el caso sigue entero para el próximo intento
            resultado.errores += 1
            logger.exception("No se pudo borrar el caso", extra={"id_caso": id_caso, "job": nombre})
    resultado.detalle = {"fotos": fotos, "versiones_s3": versiones}
    return resultado


def descarte_inactividad(engine: Engine, ctx: Contexto) -> ResultadoJob:
    """Casos del chatbot con ≥ 72 h sin actividad: se descartan con borrado físico de datos y fotos (con versiones)."""
    condicion = (
        "e.nombre_estado = ANY(:estados) "
        "AND c.ultima_actividad <= CAST(:ahora AS timestamptz) - make_interval(hours => :horas)"
    )
    return _borrar_vigentes(
        engine,
        ctx,
        "descarte_inactividad",
        f"{_CASOS} WHERE {condicion} ORDER BY c.id_caso",
        condicion,
        ACCION_DESCARTE,
        "descartado por 72 h sin actividad",
    )


DIAS_AVISO_VENCIMIENTO = 15  # igual que el Inicio del panel


def avisar_proximos_a_vencer(engine: Engine, ctx: Contexto) -> int:
    """B-10: un aviso diario a los líderes si hay casos Cerrados que vencen en los próximos 15 días."""
    with Session(engine) as sesion:
        cuantos = int(
            sesion.scalar(
                text(
                    "SELECT count(*) FROM casos c JOIN estados e ON e.id_estado = c.id_estado "
                    "WHERE e.nombre_estado = :cerrado AND c.vence_en IS NOT NULL "
                    "AND c.vence_en BETWEEN (CAST(:ahora AS timestamptz) AT TIME ZONE 'America/Bogota')::date "
                    "AND (CAST(:ahora AS timestamptz) AT TIME ZONE 'America/Bogota')::date + :dias"
                ),
                {
                    "cerrado": ESTADOS_CASO.a_nombre(EstadoCaso.CERRADO),
                    "ahora": ctx.ahora,
                    "dias": DIAS_AVISO_VENCIMIENTO,
                },
            )
            or 0
        )
        lideres = tuple(str(i) for (i,) in sesion.execute(LIDERES_ACTIVOS_SQL))
    if cuantos:
        texto = "1 caso" if cuantos == 1 else f"{cuantos} casos"
        publicar_sync(
            Aviso(
                lideres, PROXIMOS_A_VENCER, "Casos próximos a vencer",
                f"{texto} con fecha de vencimiento de conservación en menos de {DIAS_AVISO_VENCIMIENTO} días",
                f"/casos?estado=CERRADO&vencimiento={DIAS_AVISO_VENCIMIENTO}",
            )
        )  # fmt: skip
    return cuantos


def retencion(engine: Engine, ctx: Contexto) -> ResultadoJob:
    """Casos Cerrados cuya conservación venció (`vence_en` ya llegó, en hora de Colombia): borrado físico total.

    Después avisa a los líderes de los que vencen pronto (B-10); si el aviso falla, el job no falla.
    """
    resultado = _retencion(engine, ctx)
    try:
        resultado.detalle["proximos_a_vencer"] = avisar_proximos_a_vencer(engine, ctx)
    except Exception:
        logger.exception("No se pudo avisar de los casos próximos a vencer")
    return resultado


def _retencion(engine: Engine, ctx: Contexto) -> ResultadoJob:
    condicion = (
        "e.nombre_estado = :cerrado AND c.vence_en IS NOT NULL "
        "AND c.vence_en <= (CAST(:ahora AS timestamptz) AT TIME ZONE 'America/Bogota')::date"
    )
    return _borrar_vigentes(
        engine,
        ctx,
        "retencion",
        f"{_CASOS} WHERE {condicion} ORDER BY c.id_caso",
        condicion,
        ACCION_RETENCION,
        "venció su plazo de conservación",
    )


# --- barrido de alertas ---------------------------------------------------------------------------------------------


def evaluar_alertas(engine: Engine, ctx: Contexto) -> ResultadoJob:
    """Barrido de respaldo de `sp_evaluar_alerta` para todas las categorías transmisibles (P-B01)."""
    resultado = ResultadoJob("evaluar_alertas")
    try:
        with Session(engine) as sesion, sesion.begin():
            antes = {str(i) for (i,) in sesion.execute(text("SELECT id_alerta FROM alertas"))}
            sesion.execute(sp.EVALUAR_ALERTA, {"id_categoria": None, "fecha": ctx.ahora})
            todas = sesion.execute(
                text(
                    "SELECT a.id_alerta, cf.nombre_categoria FROM alertas a "
                    "JOIN categorias_fitosanitarias cf ON cf.id_categoria = a.id_categoria"
                )
            ).all()
            nuevas = [(str(i), str(c)) for i, c in todas if str(i) not in antes]
            lideres = tuple(str(i) for (i,) in sesion.execute(LIDERES_ACTIVOS_SQL))
        resultado.procesados = resultado.exitosos = 1
        resultado.detalle = {"alertas_nuevas": len(nuevas)}
        for id_alerta, categoria in nuevas:  # B-10: después del commit
            publicar_sync(
                Aviso(
                    lideres, ALERTA_GENERADA, "Alerta generada", f"Posible concentración de {categoria}",
                    f"/alertas/{id_alerta}",
                )
            )  # fmt: skip
    except Exception:
        resultado.procesados = resultado.errores = 1
        logger.exception("Falló el barrido de alertas")
    return resultado


# --- registro y ejecución -------------------------------------------------------------------------------------------

JOBS: dict[str, Callable[[Engine, Contexto], ResultadoJob]] = {
    "advertencia_inactividad": advertencia_inactividad,
    "descarte_inactividad": descarte_inactividad,
    "retencion": retencion,
    "evaluar_alertas": evaluar_alertas,
}
NECESITAN_S3 = {"descarte_inactividad", "retencion"}
NECESITAN_WHATSAPP = {"advertencia_inactividad"}


def ejecutar(nombre: str, engine: Engine, ctx: Contexto) -> ResultadoJob:
    """Corre el job con su candado. Si otra ejecución lo tiene, se omite sin error."""
    with bloqueo(engine, nombre) as obtenido:
        if not obtenido:
            logger.info("El job ya está en ejecución; se omite", extra={"job": nombre})
            return ResultadoJob(nombre, ya_en_ejecucion=True)
        return JOBS[nombre](engine, ctx)
