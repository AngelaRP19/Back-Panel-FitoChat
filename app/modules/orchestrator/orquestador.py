"""Orquestador conversacional del chatbot (guía §11, módulo 10): la máquina de estados de cada conversación.

Flujo: saludo y consentimiento → finca → cultivo → especie y zona afectada → cuestionario (lo conduce la IA) → fotos →
resumen y envío. `/salir` cancela la consulta y borra sus datos; un número desconocido se registra con consentimiento
(P-B08); cada mensaje reinicia el conteo de inactividad del caso.

El orquestador **conecta y envía mensajes**: las preguntas de síntomas las decide la IA (`AiClient`), las fotos las
valida el pipeline de imágenes y la identificación la calcula el identificador (módulo 11). Todos son interfaces.

Transacciones: `manejar` trabaja dentro de la transacción del mensaje (la abre el consumidor) y NO envía ni guarda
nada fuera de la base; pide esos efectos con `Efectos`, que `aplicar` ejecuta después de confirmar.
"""

import logging
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.catalogos_api import CATEGORIAS, ESPECIES, EspecieCultivo, TipoCultivo
from app.core.config import Settings, get_settings
from app.core.evidencias import MAXIMO_FOTOS_POR_TIPO, MAXIMO_FOTOS_UTILES
from app.core.guardrail import recomienda_tratamiento
from app.core.logging import mask_phone
from app.infrastructure.ai_client import AiClient, ContextoConsulta, ErrorIA, con_reintentos
from app.infrastructure.conversaciones_store import Conversacion, ConversacionesStore
from app.infrastructure.s3_storage_client import S3StorageClient
from app.infrastructure.whatsapp_client import WhatsAppClient
from app.modules.consumer.efectos import Efectos
from app.modules.consumer.procesador import MensajeEntrante
from app.modules.identification.reglas import ordenar
from app.modules.orchestrator import repositorio as repo
from app.modules.orchestrator import textos as t
from app.modules.orchestrator.puertos import Identificador, PipelineImagenes
from app.modules.scheduler.borrado import borrar_caso

logger = logging.getLogger(__name__)

MAXIMO_PREGUNTAS = (
    15  # tope de seguridad: la IA decide cuándo hay información suficiente, pero no puede preguntar sin fin
)
MAXIMO_LARGO_ZONA = 300
ACCION_SALIR = "BORRAR_SALIR"
ACCION_ABANDONO = "BORRAR_ABANDONO"


class Paso(StrEnum):
    INICIO = "INICIO"
    CONSENTIMIENTO = "CONSENTIMIENTO"
    NOMBRE = "NOMBRE"
    ELEGIR_FINCA = "ELEGIR_FINCA"
    ELEGIR_CULTIVO = "ELEGIR_CULTIVO"
    CULTIVO_NOMBRE = "CULTIVO_NOMBRE"
    CULTIVO_TIPO = "CULTIVO_TIPO"
    CULTIVO_ESPECIES = "CULTIVO_ESPECIES"
    ELEGIR_ESPECIE = "ELEGIR_ESPECIE"
    AFECTACION = "AFECTACION"
    ZONA = "ZONA"
    CUESTIONARIO = "CUESTIONARIO"
    FOTOS = "FOTOS"
    CONFIRMAR_ENVIO = "CONFIRMAR_ENVIO"
    CONFIRMAR_SALIR = "CONFIRMAR_SALIR"
    CONFIRMAR_ABANDONO = "CONFIRMAR_ABANDONO"


def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes ni signos: «¡Sí!» y «si» son lo mismo."""
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w/ ]", "", sin_tildes).strip()


_AFIRMATIVOS = {normalizar(x) for x in t.AFIRMATIVOS}
_NEGATIVOS = {normalizar(x) for x in t.NEGATIVOS}
_TERMINAR = {normalizar(x) for x in t.TERMINAR}


def si_o_no(texto: str) -> bool | None:
    limpio = normalizar(texto)
    return True if limpio in _AFIRMATIVOS else False if limpio in _NEGATIVOS else None


def elegir(texto: str, opciones: list[tuple[str, str]]) -> tuple[str, str] | None:
    """Elige por número («2») o por nombre («la esperanza»). Solo si es inequívoco."""
    limpio = normalizar(texto)
    if limpio.isdigit():
        n = int(limpio)
        return opciones[n - 1] if 1 <= n <= len(opciones) else None
    exactas = [o for o in opciones if normalizar(o[1]) == limpio]
    if len(exactas) == 1:
        return exactas[0]
    parecidas = [o for o in opciones if limpio and normalizar(o[1]).startswith(limpio)]
    return parecidas[0] if len(parecidas) == 1 else None


def lista_numerada(opciones: list[tuple[str, str]]) -> str:
    return "\n".join(f"{i}. {nombre}" for i, (_, nombre) in enumerate(opciones, start=1))


class SinS3:
    """Para correr en local sin bucket: no hay nada que borrar en S3."""

    def eliminar_todas_las_versiones(self, clave: str) -> int:
        return 0


@dataclass
class Dialogo:
    """Todo lo de un mensaje en curso: la sesión de la base, el mensaje, el estado y lo que se va a responder."""

    s: Session
    m: MensajeEntrante
    conv: Conversacion
    productor: repo.ProductorFila | None
    ef: Efectos

    @property
    def ctx(self) -> dict[str, Any]:
        return self.conv.contexto

    def decir(self, texto: str) -> None:
        self.ef.respuestas.append(texto)

    def ir_a(self, paso: Paso) -> None:
        self.conv.paso = paso.value

    def reiniciar(self) -> None:
        """Vuelve al inicio, sin caso ni contexto."""
        self.conv.paso, self.conv.caso_id, self.conv.contexto = Paso.INICIO.value, None, {}


class Orquestador:
    def __init__(
        self,
        store: ConversacionesStore,
        whatsapp: WhatsAppClient,
        ai: AiClient,
        pipeline: PipelineImagenes,
        identificador: Identificador,
        s3: S3StorageClient | None = None,
        ajustes: Settings | None = None,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        self.ajustes = ajustes or get_settings()
        self.dormir = dormir
        self.store = store
        self.whatsapp = whatsapp
        self.ai = ai
        self.pipeline = pipeline
        self.identificador = identificador
        self.s3: S3StorageClient = s3 if s3 is not None else cast(S3StorageClient, SinS3())
        self.pasos: dict[Paso, Callable[[Dialogo, str], None]] = {
            Paso.INICIO: self._inicio,
            Paso.CONSENTIMIENTO: self._consentimiento,
            Paso.NOMBRE: self._nombre,
            Paso.ELEGIR_FINCA: self._elegir_finca,
            Paso.ELEGIR_CULTIVO: self._elegir_cultivo,
            Paso.CULTIVO_NOMBRE: self._cultivo_nombre,
            Paso.CULTIVO_TIPO: self._cultivo_tipo,
            Paso.CULTIVO_ESPECIES: self._cultivo_especies,
            Paso.ELEGIR_ESPECIE: self._elegir_especie,
            Paso.AFECTACION: self._afectacion,
            Paso.ZONA: self._zona,
            Paso.CUESTIONARIO: self._cuestionario,
            Paso.FOTOS: self._fotos,
            Paso.CONFIRMAR_ENVIO: self._confirmar_envio,
            Paso.CONFIRMAR_SALIR: self._confirmar_salir,
            Paso.CONFIRMAR_ABANDONO: self._confirmar_abandono,
        }

    # --- Punto de entrada ------------------------------------------------------------------------

    def manejar(self, s: Session, m: MensajeEntrante) -> Efectos:
        """Procesa un mensaje dentro de la transacción del consumidor. No envía ni guarda nada fuera de la base."""
        conv = self.store.obtener(m.telefono) or Conversacion(m.telefono, Paso.INICIO.value)
        if conv.paso not in {p.value for p in Paso}:  # estado de una versión anterior: se empieza de nuevo
            conv = Conversacion(m.telefono, Paso.INICIO.value)
        d = Dialogo(s, m, conv, repo.productor_por_telefono(s, m.telefono), Efectos())
        texto = self._texto(m)

        if conv.caso_id and repo.caso_abierto(s, conv.caso_id) is None:  # lo descartó el job de 72 h o ya no existe
            d.decir(t.CONSULTA_VENCIDA)
            d.reiniciar()
        if d.productor is not None and not conv.caso_id:  # el estado se perdió pero la consulta sigue en la base
            abierto = repo.caso_abierto_del_productor(s, d.productor.id)
            if abierto is not None and conv.paso == Paso.INICIO.value:
                conv.caso_id = abierto.id
        if conv.caso_id:
            repo.tocar(s, conv.caso_id)  # cada mensaje reinicia el conteo de inactividad

        if normalizar(texto) == "/salir":
            self._pedir_salir(d)
        else:
            paso = Paso(conv.paso)
            self.pasos[paso](d, texto)

        if not d.ef.borrar_conversacion:
            d.conv.ultima_actividad = time.time()
            d.ef.conversacion = d.conv
        return d.ef

    def aplicar(self, m: MensajeEntrante, efectos: Efectos) -> None:
        """Después de confirmar la transacción: guarda el estado y envía las respuestas, en ese orden."""
        if efectos.borrar_conversacion:
            self.store.borrar(m.telefono)
        elif efectos.conversacion is not None:
            self.store.guardar(efectos.conversacion)
        for texto in efectos.respuestas:
            resultado = self.whatsapp.enviar_texto(m.telefono, texto)
            if not resultado.exito:
                logger.error(
                    "No se pudo enviar la respuesta", extra={"wamid": m.wamid, "celular": mask_phone(m.telefono)}
                )

    @staticmethod
    def _texto(m: MensajeEntrante) -> str:
        if m.tipo == "text":
            return str(m.contenido.get("text", "")).strip()
        return str(m.contenido.get("caption", "")).strip()

    # --- Inicio, consentimiento y registro -------------------------------------------------------

    def _inicio(self, d: Dialogo, texto: str) -> None:
        p = d.productor
        if p is None:
            d.decir(t.SALUDO)
            d.decir(t.CONSENTIMIENTO)
            d.ir_a(Paso.CONSENTIMIENTO)
            return
        if not p.activo:
            d.decir(t.CUENTA_DESHABILITADA)
            return
        d.decir(t.SALUDO_CON_NOMBRE.format(nombre=p.nombre.split()[0]))
        if p.consentimiento is None:  # registrado desde el panel: falta que acepte el tratamiento de sus datos (D-12)
            d.decir(t.CONSENTIMIENTO)
            d.ir_a(Paso.CONSENTIMIENTO)
            return
        self._comenzar(d)

    def _consentimiento(self, d: Dialogo, texto: str) -> None:
        respuesta = si_o_no(texto)
        if respuesta is None:
            d.decir(t.SI_O_NO)
        elif not respuesta:
            d.decir(t.CONSENTIMIENTO_NEGADO)
            d.reiniciar()
        elif d.productor is None:
            d.decir(t.PEDIR_NOMBRE)
            d.ir_a(Paso.NOMBRE)
        else:
            repo.dar_consentimiento(d.s, d.productor.id)
            self._comenzar(d)

    def _nombre(self, d: Dialogo, texto: str) -> None:
        nombre = " ".join(texto.split())
        if not 2 <= len(nombre) <= 100 or not re.search(r"[^\W\d_]", nombre):
            d.decir(t.NOMBRE_INVALIDO)
            return
        id_productor = repo.registrar_productor(d.s, nombre, d.m.telefono)
        repo.registrar_auditoria(
            d.s, "REGISTRO_PRODUCTOR", f"Productor {id_productor} registrado por WhatsApp con consentimiento"
        )
        d.productor = repo.productor_por_telefono(d.s, d.m.telefono)
        d.decir(t.BIENVENIDA_NUEVO.format(nombre=nombre.split()[0]))
        self._comenzar(d)

    def _comenzar(self, d: Dialogo) -> None:
        assert d.productor is not None
        abierto = repo.caso_abierto_del_productor(d.s, d.productor.id)
        if abierto is not None:  # RQF32: ya tiene una consulta en curso; se le pregunta si la abandona
            d.ctx["caso_existente"] = abierto.id
            d.decir(t.CONFIRMAR_ABANDONO)
            d.ir_a(Paso.CONFIRMAR_ABANDONO)
            return
        self._pedir_finca(d)

    # --- Finca, cultivo, especie y zona ----------------------------------------------------------

    def _pedir_finca(self, d: Dialogo) -> None:
        assert d.productor is not None
        fincas = repo.fincas(d.s, d.productor.id)
        if not fincas:
            d.decir(t.SIN_FINCAS)
            d.reiniciar()
        elif len(fincas) == 1:
            d.ctx.update(finca_id=fincas[0][0], finca_nombre=fincas[0][1])
            d.decir(t.FINCA_UNICA.format(nombre=fincas[0][1]))
            self._pedir_cultivo(d)
        else:
            d.ctx["opciones"] = [list(f) for f in fincas]
            d.decir(t.ELEGIR_FINCA.format(lista=lista_numerada(fincas)))
            d.ir_a(Paso.ELEGIR_FINCA)

    def _elegir_finca(self, d: Dialogo, texto: str) -> None:
        opciones = [(a, b) for a, b in d.ctx.get("opciones", [])]
        elegida = elegir(texto, opciones)
        if elegida is None:
            d.decir(t.OPCION_INVALIDA.format(ayuda=t.RESPONDE_CON_NUMERO))
            d.decir(t.ELEGIR_FINCA.format(lista=lista_numerada(opciones)))
            return
        d.ctx.update(finca_id=elegida[0], finca_nombre=elegida[1])
        self._pedir_cultivo(d)

    def _pedir_cultivo(self, d: Dialogo) -> None:
        cultivos = repo.cultivos(d.s, d.ctx["finca_id"])
        if not cultivos:  # RQF30: en el primer uso se registra el cultivo desde WhatsApp
            d.decir(t.SIN_CULTIVOS)
            d.ir_a(Paso.CULTIVO_NOMBRE)
        elif len(cultivos) == 1:
            self._fijar_cultivo(d, cultivos[0])
            d.decir(t.CULTIVO_UNICO.format(nombre=cultivos[0].nombre))
            self._pedir_especie(d)
        else:
            opciones = [(c.id, c.nombre) for c in cultivos]
            d.ctx["opciones"] = [list(o) for o in opciones]
            d.decir(t.ELEGIR_CULTIVO.format(lista=lista_numerada(opciones)))
            d.ir_a(Paso.ELEGIR_CULTIVO)

    @staticmethod
    def _fijar_cultivo(d: Dialogo, cultivo: repo.CultivoFila) -> None:
        d.ctx.update(cultivo_id=cultivo.id, cultivo_nombre=cultivo.nombre, especies=cultivo.especies)

    def _elegir_cultivo(self, d: Dialogo, texto: str) -> None:
        opciones = [(a, b) for a, b in d.ctx.get("opciones", [])]
        elegido = elegir(texto, opciones)
        if elegido is None:
            d.decir(t.OPCION_INVALIDA.format(ayuda=t.RESPONDE_CON_NUMERO))
            d.decir(t.ELEGIR_CULTIVO.format(lista=lista_numerada(opciones)))
            return
        cultivo = next(c for c in repo.cultivos(d.s, d.ctx["finca_id"]) if c.id == elegido[0])
        self._fijar_cultivo(d, cultivo)
        self._pedir_especie(d)

    def _cultivo_nombre(self, d: Dialogo, texto: str) -> None:
        nombre = " ".join(texto.split())
        if not nombre or len(nombre) > 100 or repo.nombre_de_cultivo_en_uso(d.s, d.ctx["finca_id"], nombre):
            d.decir(t.NOMBRE_CULTIVO_INVALIDO)
            return
        d.ctx["nuevo_cultivo"] = {"nombre": nombre}
        d.decir(t.PEDIR_TIPO_CULTIVO)
        d.ir_a(Paso.CULTIVO_TIPO)

    def _cultivo_tipo(self, d: Dialogo, texto: str) -> None:
        limpio = normalizar(texto)
        tipo = (
            TipoCultivo.INVERNADERO
            if limpio in {"1", "invernadero"}
            else TipoCultivo.CAMPO_ABIERTO
            if limpio in {"2", "campo abierto", "campo", "abierto"}
            else None
        )
        if tipo is None:
            d.decir(t.OPCION_INVALIDA.format(ayuda=t.RESPONDE_CON_NUMERO))
            d.decir(t.PEDIR_TIPO_CULTIVO)
            return
        d.ctx["nuevo_cultivo"]["tipo"] = tipo.value
        d.decir(
            t.PEDIR_ESPECIES_CULTIVO.format(
                lista=lista_numerada([(e.value, ESPECIES.a_nombre(e)) for e in EspecieCultivo])
            )
        )
        d.ir_a(Paso.CULTIVO_ESPECIES)

    def _cultivo_especies(self, d: Dialogo, texto: str) -> None:
        todas = list(EspecieCultivo)
        numeros = {int(n) for n in re.findall(r"\d+", texto)}
        elegidas = [e for i, e in enumerate(todas, start=1) if i in numeros]
        if not elegidas:
            d.decir(t.ESPECIES_INVALIDAS)
            return
        nuevo = d.ctx.pop("nuevo_cultivo")
        id_cultivo = repo.crear_cultivo(
            d.s, d.ctx["finca_id"], nuevo["nombre"], nuevo["tipo"], [str(e) for e in elegidas]
        )
        d.ctx.update(cultivo_id=id_cultivo, cultivo_nombre=nuevo["nombre"], especies=[str(e) for e in elegidas])
        d.decir(t.CULTIVO_CREADO.format(nombre=nuevo["nombre"]))
        self._pedir_especie(d)

    def _pedir_especie(self, d: Dialogo) -> None:
        especies: list[str] = d.ctx["especies"]
        if len(especies) == 1:
            d.ctx["especie"] = especies[0]
            self._pedir_afectacion(d)
        else:
            opciones = [(e, ESPECIES.a_nombre(e)) for e in especies]
            d.ctx["opciones"] = [list(o) for o in opciones]
            d.decir(t.ELEGIR_ESPECIE.format(lista=lista_numerada(opciones)))
            d.ir_a(Paso.ELEGIR_ESPECIE)

    def _elegir_especie(self, d: Dialogo, texto: str) -> None:
        opciones = [(a, b) for a, b in d.ctx.get("opciones", [])]
        elegida = elegir(texto, opciones)
        if elegida is None:
            d.decir(t.OPCION_INVALIDA.format(ayuda=t.RESPONDE_CON_NUMERO))
            d.decir(t.ELEGIR_ESPECIE.format(lista=lista_numerada(opciones)))
            return
        d.ctx["especie"] = elegida[0]
        self._pedir_afectacion(d)

    @staticmethod
    def _pedir_afectacion(d: Dialogo) -> None:
        d.decir(t.PEDIR_AFECTACION)
        d.ir_a(Paso.AFECTACION)

    def _afectacion(self, d: Dialogo, texto: str) -> None:
        limpio = normalizar(texto)
        if limpio in {"1", "todo", "todo el cultivo", "total"}:  # no se asume: el productor lo confirma (RQF33)
            self._abrir_caso(d, t.ZONA_TOTAL)
        elif limpio in {"2", "una parte", "parte", "parcial"}:
            d.decir(t.PEDIR_ZONA)
            d.ir_a(Paso.ZONA)
        else:
            d.decir(t.OPCION_INVALIDA.format(ayuda=t.RESPONDE_CON_NUMERO))
            d.decir(t.PEDIR_AFECTACION)

    def _zona(self, d: Dialogo, texto: str) -> None:
        zona = " ".join(texto.split())
        if not zona or len(zona) > MAXIMO_LARGO_ZONA:
            d.decir(t.ZONA_INVALIDA)
            return
        self._abrir_caso(d, zona)

    # --- Caso y cuestionario (lo conduce la IA) --------------------------------------------------

    def _abrir_caso(self, d: Dialogo, zona: str) -> None:
        assert d.productor is not None
        d.conv.caso_id = repo.crear_caso(d.s, d.productor.id, d.ctx["cultivo_id"], d.ctx["especie"], zona)
        d.ctx.update(zona=zona, n_preguntas=0, pregunta_actual=None, cuestionario_completo=False)
        d.decir(t.CUESTIONARIO_INICIO)
        self._turno_ia(d, None)

    def _cuestionario(self, d: Dialogo, texto: str) -> None:
        if not texto:  # una foto, un audio…: se repite la pregunta
            if d.ctx.get("pregunta_actual"):
                d.decir(d.ctx["pregunta_actual"])
            return
        self._turno_ia(d, texto)

    def _turno_ia(self, d: Dialogo, mensaje: str | None) -> None:
        assert d.conv.caso_id is not None
        pendiente = d.ctx.get("pregunta_actual")
        contexto = ContextoConsulta(
            especie=d.ctx["especie"],
            cultivo=d.ctx.get("cultivo_nombre", ""),
            zona_afectada=d.ctx.get("zona", ""),
            historial=repo.historial(d.s, d.conv.caso_id),
            pregunta_pendiente=pendiente,
        )
        try:
            turno = con_reintentos(
                lambda: self.ai.conducir(contexto, mensaje),
                self.ajustes.ia_reintentos,
                self.ajustes.ia_pausa_reintento_segundos,
                self.dormir,
            )
        except ErrorIA:  # la IA no responde: se pide perdón y se sigue con las fotos; el técnico revisa el caso
            logger.error("La IA no respondió; se salta el cuestionario", extra={"id_caso": d.conv.caso_id})
            d.decir(t.IA_NO_DISPONIBLE)
            d.ctx.update(pregunta_actual=None, cuestionario_completo=True)
            self._pedir_fotos(d)
            return
        if mensaje is not None and turno.fuera_de_dominio:  # se rechaza con un mensaje fijo y se repite el paso
            d.decir(t.FUERA_DE_DOMINIO)
            if pendiente:
                d.decir(pendiente)
            return
        if mensaje is not None and pendiente and turno.relevante:  # lo irrelevante no se guarda en el caso
            repo.guardar_respuesta(d.s, d.conv.caso_id, pendiente, mensaje)
        bloqueada = turno.pregunta is not None and recomienda_tratamiento(turno.pregunta)
        if turno.completo or turno.pregunta is None or bloqueada or d.ctx["n_preguntas"] >= MAXIMO_PREGUNTAS:
            if bloqueada:  # la IA se salió de su papel: se corta el cuestionario y que decida el técnico
                logger.warning("La IA propuso un texto con tratamientos; se omitió", extra={"id_caso": d.conv.caso_id})
                d.decir(t.PREGUNTA_BLOQUEADA)
            d.ctx.update(pregunta_actual=None, cuestionario_completo=True)
            self._pedir_fotos(d)
            return
        d.ctx["pregunta_actual"] = turno.pregunta
        d.ctx["n_preguntas"] += 1
        d.decir(turno.pregunta)
        d.ir_a(Paso.CUESTIONARIO)

    # --- Fotos y envío ---------------------------------------------------------------------------

    @staticmethod
    def _pedir_fotos(d: Dialogo) -> None:
        d.decir(t.PEDIR_FOTOS.format(por_tipo=MAXIMO_FOTOS_POR_TIPO, total=MAXIMO_FOTOS_UTILES))
        d.ir_a(Paso.FOTOS)

    def _fotos(self, d: Dialogo, texto: str) -> None:
        assert d.conv.caso_id is not None
        if d.m.tipo == "image":
            self._foto(d, texto)
        elif normalizar(texto) in _TERMINAR:
            if sum(repo.fotos_por_tipo(d.s, d.conv.caso_id).values()) < 1:
                d.decir(t.FALTA_FOTO)
            else:
                self._resumen(d)
        else:
            d.decir(t.SOLO_FOTOS)

    def _foto(self, d: Dialogo, descripcion: str) -> None:
        assert d.conv.caso_id is not None
        por_tipo = repo.fotos_por_tipo(d.s, d.conv.caso_id)
        if sum(por_tipo.values()) >= MAXIMO_FOTOS_UTILES:
            d.decir(t.LIMITE_TOTAL.format(total=MAXIMO_FOTOS_UTILES))
            self._resumen(d)
            return
        resultado = self.pipeline.procesar(d.conv.caso_id, str(d.m.contenido.get("media_id", "")), descripcion or None)
        if not resultado.util or resultado.clave_s3 is None:
            d.decir(
                t.FOTO_RECHAZADA.format(motivo=resultado.motivo) if resultado.motivo else t.FOTO_RECHAZADA_SIN_MOTIVO
            )
            return
        tipo = resultado.tipo or "HOJA"
        if por_tipo.get(tipo, 0) >= MAXIMO_FOTOS_POR_TIPO:
            self.pipeline.eliminar(resultado.clave_s3)  # ya estaba guardada: no se usa y no debe quedar huérfana
            d.decir(t.LIMITE_POR_TIPO.format(por_tipo=MAXIMO_FOTOS_POR_TIPO))
            return
        repo.guardar_foto(d.s, d.conv.caso_id, tipo, resultado.clave_s3, descripcion or None)
        total = sum(por_tipo.values()) + 1
        d.decir(t.FOTO_ACEPTADA.format(n=total, total=MAXIMO_FOTOS_UTILES))
        if total >= MAXIMO_FOTOS_UTILES:
            d.decir(t.LIMITE_TOTAL.format(total=MAXIMO_FOTOS_UTILES))
            self._resumen(d)

    def _resumen(self, d: Dialogo) -> None:
        assert d.conv.caso_id is not None
        fotos = sum(repo.fotos_por_tipo(d.s, d.conv.caso_id).values())
        d.decir(
            t.RESUMEN.format(
                finca=d.ctx.get("finca_nombre", ""),
                cultivo=d.ctx.get("cultivo_nombre", ""),
                especie=ESPECIES.a_nombre(d.ctx["especie"]),
                zona=d.ctx.get("zona", ""),
                respuestas=len(repo.historial(d.s, d.conv.caso_id)),
                fotos=fotos,
            )
        )
        d.ir_a(Paso.CONFIRMAR_ENVIO)

    def _confirmar_envio(self, d: Dialogo, texto: str) -> None:
        respuesta = si_o_no(texto)
        if respuesta is None:
            d.decir(t.SI_O_NO)
        elif not respuesta:
            d.decir(t.NO_ENVIADO)
            d.ir_a(Paso.FOTOS)
        else:
            self._enviar(d)

    def _enviar(self, d: Dialogo) -> None:
        """Pendiente de revisión solo con cuestionario completo y al menos una foto válida."""
        assert d.productor is not None and d.conv.caso_id is not None
        if not d.ctx.get("cuestionario_completo") or sum(repo.fotos_por_tipo(d.s, d.conv.caso_id).values()) < 1:
            d.decir(t.FALTA_FOTO)
            d.ir_a(Paso.FOTOS)
            return
        identificacion = self.identificador.identificar(d.s, d.conv.caso_id)
        if identificacion is not None:
            repo.guardar_identificacion(
                d.s,
                d.conv.caso_id,
                identificacion.tipo_resultado,
                identificacion.explicacion,
                identificacion.porcentajes,
                identificacion.detalle,
            )
        repo.enviar_al_panel(d.s, d.conv.caso_id, d.productor.id)
        d.decir(t.ENVIADO)
        if identificacion is not None and identificacion.porcentajes:
            primeras = [CATEGORIAS.a_nombre(c) for c, _ in ordenar(identificacion.porcentajes)[:2]]
            if identificacion.tipo_resultado == "PRELIMINAR":
                d.decir(t.RESULTADO_PRELIMINAR.format(resultado=primeras[0]))
            elif identificacion.tipo_resultado == "AMBIGUO" and len(primeras) == 2:
                d.decir(t.RESULTADO_AMBIGUO.format(primera=primeras[0], segunda=primeras[1]))
        d.ef.borrar_conversacion = True  # la consulta terminó: ya es del técnico

    # --- /salir, abandonar y retomar -------------------------------------------------------------

    @staticmethod
    def _ahora(d: Dialogo) -> datetime:
        ahora = d.s.scalar(text("SELECT fn_ahora()"))
        assert isinstance(ahora, datetime)
        return ahora

    def _pedir_salir(self, d: Dialogo) -> None:
        if not d.conv.caso_id:
            d.decir(t.NO_HAY_CONSULTA)
            return
        d.ctx["volver_a"] = d.conv.paso
        d.decir(t.CONFIRMAR_SALIR)
        d.ir_a(Paso.CONFIRMAR_SALIR)

    def _confirmar_salir(self, d: Dialogo, texto: str) -> None:
        respuesta = si_o_no(texto)
        if respuesta is None:
            d.decir(t.SI_O_NO)
        elif respuesta:
            assert d.conv.caso_id is not None
            borrar_caso(
                d.s,
                self.s3,
                d.conv.caso_id,
                d.s.scalar(text("SELECT fn_ahora()")),
                ACCION_SALIR,
                "el productor canceló la consulta",
            )
            d.decir(t.SALIR_HECHO)
            d.ef.borrar_conversacion = True
        else:
            d.decir(t.SALIR_CANCELADO)
            d.ir_a(Paso(d.ctx.pop("volver_a", Paso.INICIO.value)))
            self._repetir(d)

    def _repetir(self, d: Dialogo) -> None:
        """Vuelve a mostrar lo que el paso actual espera, después de cancelar un /salir."""
        if d.conv.paso == Paso.CUESTIONARIO.value and d.ctx.get("pregunta_actual"):
            d.decir(d.ctx["pregunta_actual"])
        elif d.conv.paso == Paso.FOTOS.value:
            d.decir(t.PEDIR_FOTOS.format(por_tipo=MAXIMO_FOTOS_POR_TIPO, total=MAXIMO_FOTOS_UTILES))
        elif d.conv.paso == Paso.CONFIRMAR_ENVIO.value:
            self._resumen(d)

    def _confirmar_abandono(self, d: Dialogo, texto: str) -> None:
        respuesta = si_o_no(texto)
        id_existente: str = d.ctx.get("caso_existente", "")
        if respuesta is None:
            d.decir(t.SI_O_NO)
        elif respuesta:
            borrar_caso(
                d.s,
                self.s3,
                id_existente,
                d.s.scalar(text("SELECT fn_ahora()")),
                ACCION_ABANDONO,
                "el productor abandonó la consulta",
            )
            d.decir(t.ABANDONO_HECHO)
            d.conv.caso_id = None
            d.ctx.pop("caso_existente", None)
            self._pedir_finca(d)
        else:
            self._continuar(d, id_existente)

    def _continuar(self, d: Dialogo, id_caso: str) -> None:
        """Retoma la consulta abierta: reconstruye el contexto desde la base y le devuelve la palabra a la IA."""
        caso = repo.caso_abierto(d.s, id_caso)
        if caso is None:
            d.reiniciar()
            self._pedir_finca(d)
            return
        d.conv.caso_id = caso.id
        d.ctx.update(
            cultivo_id=caso.id_cultivo,
            cultivo_nombre=caso.nombre_cultivo,
            finca_nombre=caso.nombre_finca,
            especie=caso.especie,
            zona=caso.zona,
            n_preguntas=len(repo.historial(d.s, caso.id)),
            pregunta_actual=None,
            cuestionario_completo=False,
        )
        d.ctx.pop("caso_existente", None)
        d.decir(t.CONTINUAR_EXISTENTE)
        self._turno_ia(d, None)
