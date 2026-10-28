"""Orquestador conversacional del chatbot (guía §11, módulo 10): la máquina de estados de cada conversación.

Flujo: saludo y consentimiento → finca (elegir una guardada o registrar una nueva: nombre, vereda y ubicación) → lote
(elegir o registrar: nombre, tipo, cultivos y ubicación tomada dentro del lote) → especie y zona afectada → cuestionario
(lo conduce la IA) → fotos → resumen y envío. Las preguntas van con botones o listas y las ubicaciones se piden con el
botón nativo de WhatsApp; el bot muestra el punto en el mapa y pide confirmarlo. `/salir` cancela la consulta y borra
sus datos; un número desconocido se registra con consentimiento (P-B08); cada mensaje reinicia el conteo de
inactividad del caso.

El orquestador **conecta y envía mensajes**: las preguntas de síntomas las decide la IA (`AiClient`), las fotos las
valida el pipeline de imágenes y la identificación la calcula el identificador (módulo 11). Todos son interfaces.

Transacciones: `manejar` trabaja dentro de la transacción del mensaje (la abre el consumidor) y NO envía ni guarda
nada fuera de la base; pide esos efectos con `Efectos`, que `aplicar` ejecuta después de confirmar.
"""

import logging
import math
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from itertools import combinations
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.catalogos_api import CATEGORIAS, ESPECIES, EspecieCultivo, TipoCultivo, normalizar_codigo
from app.core.codigos import codigo_caso
from app.core.config import Settings, get_settings
from app.core.evidencias import MAXIMO_FOTOS_POR_TIPO, MAXIMO_FOTOS_UTILES
from app.core.guardrail import recomienda_tratamiento
from app.core.logging import mask_phone
from app.infrastructure.ai_client import AiClient, ContextoConsulta, ErrorIA, con_reintentos
from app.infrastructure.conversaciones_store import Conversacion, ConversacionesStore
from app.infrastructure.s3_storage_client import S3StorageClient
from app.infrastructure.whatsapp_client import WhatsAppClient
from app.infrastructure.whatsapp_mensajes import (
    MAXIMO_FILAS,
    Botones,
    Lista,
    Mensaje,
    Opcion,
    PedirUbicacion,
    Ubicacion,
    texto_de,
)
from app.modules.consumer.efectos import Efectos
from app.modules.consumer.procesador import MensajeEntrante
from app.modules.identification.reglas import ordenar
from app.modules.orchestrator import repositorio as repo
from app.modules.orchestrator import textos as t
from app.modules.orchestrator.puertos import Identificador, PipelineImagenes
from app.modules.panel_api.fincas.schemas import LATITUD_ESPERADA
from app.modules.panel_api.notificaciones.service import (
    CASO_SIN_TECNICO,
    LIDERES_ACTIVOS_SQL,
    Aviso,
    publicar_sync,
)
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
    FINCA_NOMBRE = "FINCA_NOMBRE"
    FINCA_VEREDA = "FINCA_VEREDA"
    FINCA_UBICACION = "FINCA_UBICACION"
    FINCA_CONFIRMAR_UBICACION = "FINCA_CONFIRMAR_UBICACION"
    ELEGIR_CULTIVO = "ELEGIR_CULTIVO"
    CULTIVO_NOMBRE = "CULTIVO_NOMBRE"
    CULTIVO_TIPO = "CULTIVO_TIPO"
    CULTIVO_ESPECIES = "CULTIVO_ESPECIES"
    CULTIVO_UBICACION = "CULTIVO_UBICACION"
    CULTIVO_CONFIRMAR_UBICACION = "CULTIVO_CONFIRMAR_UBICACION"
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


# --- Opciones de los botones y listas ------------------------------------------------------------
# Los id son lo que el productor podría escribir: el mismo código entiende el botón y el texto.
SI, NO, OTRA, NUEVA, NUEVO, LISTO = "si", "no", "otra", "nueva", "nuevo", "listo"
_NUEVA = {normalizar(x) for x in t.NUEVA}
_SALIR = {normalizar(x) for x in t.SALIR}


def si_no(texto: str, si: str = t.BOTON_SI, no: str = t.BOTON_NO) -> Botones:
    return Botones(texto, (Opcion(SI, si), Opcion(NO, no)))


MENU_CONSENTIMIENTO = si_no(t.CONSENTIMIENTO, t.BOTON_ACEPTO, t.BOTON_NO_ACEPTO)
MENU_TIPO_CULTIVO = Botones(
    t.PEDIR_TIPO_CULTIVO, (Opcion("1", t.BOTON_INVERNADERO), Opcion("2", t.BOTON_CAMPO_ABIERTO))
)
MENU_AFECTACION = Botones(t.PEDIR_AFECTACION, (Opcion("1", t.BOTON_TODO), Opcion("2", t.BOTON_UNA_PARTE)))


def con_listo(texto: str) -> Botones:
    return Botones(texto, (Opcion(LISTO, t.BOTON_LISTO),))


def menu_especie(opciones: list[tuple[str, str]]) -> Botones:
    """La especie afectada: una por botón (las de un lote son máximo tres)."""
    return Botones(t.ELEGIR_ESPECIE, tuple(Opcion(str(n), nombre) for n, (_, nombre) in enumerate(opciones, start=1)))


def menu_especies_cultivo(lote: str) -> Lista:
    """Los cultivos del lote: cada combinación es una fila (id «1,3» = la primera y la tercera especie)."""
    todas = list(EspecieCultivo)
    filas = []
    for r in range(1, len(todas) + 1):
        for combinacion in combinations(range(1, len(todas) + 1), r):
            nombres = [ESPECIES.a_nombre(todas[i - 1]) for i in combinacion]
            titulo = "Las tres" if len(nombres) == 3 else " y ".join([nombres[0], *[n.lower() for n in nombres[1:]]])
            filas.append(Opcion(",".join(map(str, combinacion)), titulo))
    return Lista(t.PEDIR_ESPECIES_CULTIVO.format(lote=lote), t.VER_CULTIVOS, tuple(filas), titulo_seccion="Cultivos")


# --- Ubicaciones ---------------------------------------------------------------------------------
DISTANCIA_LOTE_FINCA_KM = 2.0  # más lejos que esto, se avisa que el lote debería estar dentro de la finca


def en_colombia(latitud: float, longitud: float) -> bool:
    return -4.3 <= latitud <= 13.6 and -82.0 <= longitud <= -66.8


def dentro_de_zetaquira(latitud: float, longitud: float) -> bool:
    """El mismo criterio del panel para avisar (sin bloquear) de coordenadas fuera de la zona."""
    return LATITUD_ESPERADA[0] <= latitud <= LATITUD_ESPERADA[1] and longitud < 0


def distancia_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distancia sobre la Tierra (haversine)."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


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

    def decir(self, mensaje: Mensaje) -> None:
        self.ef.respuestas.append(mensaje)

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
            Paso.FINCA_NOMBRE: self._finca_nombre,
            Paso.FINCA_VEREDA: self._finca_vereda,
            Paso.FINCA_UBICACION: self._finca_ubicacion,
            Paso.FINCA_CONFIRMAR_UBICACION: self._finca_confirmar_ubicacion,
            Paso.ELEGIR_CULTIVO: self._elegir_cultivo,
            Paso.CULTIVO_NOMBRE: self._cultivo_nombre,
            Paso.CULTIVO_TIPO: self._cultivo_tipo,
            Paso.CULTIVO_ESPECIES: self._cultivo_especies,
            Paso.CULTIVO_UBICACION: self._cultivo_ubicacion,
            Paso.CULTIVO_CONFIRMAR_UBICACION: self._cultivo_confirmar_ubicacion,
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

        if normalizar(texto) in _SALIR:  # en cualquier paso: cancelar y empezar de nuevo
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
        for aviso in efectos.avisos:
            publicar_sync(aviso)
        for mensaje in efectos.respuestas:
            if isinstance(mensaje, str):
                resultado = self.whatsapp.enviar_texto(m.telefono, mensaje)
            else:
                resultado = self.whatsapp.enviar_interactivo(m.telefono, mensaje)
                if not resultado.exito:  # Meta rechazó el interactivo: va en texto, con las opciones numeradas
                    logger.warning(
                        "Meta rechazó el mensaje interactivo; se envía como texto",
                        extra={"wamid": m.wamid, "detalle": resultado.error},
                    )
                    resultado = self.whatsapp.enviar_texto(m.telefono, texto_de(mensaje))
            if not resultado.exito:
                logger.error(
                    "No se pudo enviar la respuesta", extra={"wamid": m.wamid, "celular": mask_phone(m.telefono)}
                )

    @staticmethod
    def _texto(m: MensajeEntrante) -> str:
        if m.tipo == "text":
            return str(m.contenido.get("text", "")).strip()
        if m.tipo in {"interactive", "button"}:  # botón o fila de lista: su id es la respuesta
            return str(m.contenido.get("id", "")).strip()
        return str(m.contenido.get("caption", "")).strip()

    # --- Inicio, consentimiento y registro -------------------------------------------------------

    def _inicio(self, d: Dialogo, texto: str) -> None:
        p = d.productor
        if p is None:
            d.decir(t.SALUDO)
            d.decir(MENU_CONSENTIMIENTO)
            d.ir_a(Paso.CONSENTIMIENTO)
            return
        if not p.activo:
            d.decir(t.CUENTA_DESHABILITADA)
            return
        d.decir(t.SALUDO_CON_NOMBRE.format(nombre=p.nombre.split()[0]))
        if p.consentimiento is None:  # registrado desde el panel: falta que acepte el tratamiento de sus datos (D-12)
            d.decir(MENU_CONSENTIMIENTO)
            d.ir_a(Paso.CONSENTIMIENTO)
            return
        self._comenzar(d)

    def _consentimiento(self, d: Dialogo, texto: str) -> None:
        respuesta = si_o_no(texto)
        if respuesta is None:
            d.decir(si_no(t.SI_O_NO, t.BOTON_ACEPTO, t.BOTON_NO_ACEPTO))
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
            d.decir(si_no(t.CONFIRMAR_ABANDONO))
            d.ir_a(Paso.CONFIRMAR_ABANDONO)
            return
        self._pedir_finca(d)

    # --- Finca: elegir una guardada o registrar una nueva -----------------------------------------

    def _pedir_finca(self, d: Dialogo) -> None:
        assert d.productor is not None
        fincas = repo.fincas(d.s, d.productor.id)
        if not fincas:
            d.decir(t.SIN_FINCAS)
            self._pedir_nombre_finca(d)
            return
        d.ctx["opciones"] = [list(f) for f in fincas]
        d.decir(self._menu_fincas(d, fincas))
        d.ir_a(Paso.ELEGIR_FINCA)

    @staticmethod
    def _menu_fincas(d: Dialogo, fincas: list[tuple[str, str]]) -> Mensaje:
        if len(fincas) >= MAXIMO_FILAS:  # no caben en una lista con la opción de registrar: texto numerado
            return t.ELEGIR_FINCA_TEXTO.format(lista=lista_numerada(fincas))
        veredas = repo.veredas_de_fincas(d.s, [i for i, _ in fincas])
        filas = [Opcion(str(n), nombre, veredas.get(i)) for n, (i, nombre) in enumerate(fincas, start=1)]
        filas.append(Opcion(NUEVA, t.FILA_FINCA_NUEVA, t.DESCRIPCION_FINCA_NUEVA))
        return Lista(t.ELEGIR_FINCA, t.VER_FINCAS, tuple(filas), titulo_seccion="Tus fincas")

    def _elegir_finca(self, d: Dialogo, texto: str) -> None:
        opciones = [(a, b) for a, b in d.ctx.get("opciones", [])]
        if normalizar(texto) in _NUEVA:
            self._pedir_nombre_finca(d)
            return
        elegida = elegir(texto, opciones)
        if elegida is None:
            d.decir(t.OPCION_INVALIDA.format(ayuda=t.RESPONDE_CON_NUMERO))
            d.decir(self._menu_fincas(d, opciones))
            return
        d.ctx.update(finca_id=elegida[0], finca_nombre=elegida[1])
        self._pedir_cultivo(d)

    @staticmethod
    def _pedir_nombre_finca(d: Dialogo) -> None:
        d.ctx["nueva_finca"] = {}
        d.decir(t.PEDIR_NOMBRE_FINCA)
        d.ir_a(Paso.FINCA_NOMBRE)

    def _finca_nombre(self, d: Dialogo, texto: str) -> None:
        nombre = " ".join(texto.split())
        if not 2 <= len(nombre) <= 100 or not re.search(r"[^\W\d_]", nombre):
            d.decir(t.NOMBRE_FINCA_INVALIDO)
            return
        d.ctx["nueva_finca"] = {"nombre": nombre}
        self._pedir_vereda(d)

    @staticmethod
    def _pedir_vereda(d: Dialogo) -> None:
        finca = d.ctx["nueva_finca"]["nombre"]
        veredas = repo.veredas(d.s)[: MAXIMO_FILAS - 1]
        d.ctx["opciones"] = [[v, v] for v in veredas]
        if veredas:
            filas = [Opcion(str(n), v) for n, v in enumerate(veredas, start=1)]
            filas.append(Opcion(OTRA, t.FILA_OTRA_VEREDA, t.DESCRIPCION_OTRA_VEREDA))
            d.decir(Lista(t.PEDIR_VEREDA.format(finca=finca), t.VER_VEREDAS, tuple(filas), titulo_seccion="Veredas"))
        else:
            d.decir(t.PEDIR_VEREDA_ESCRITA.format(finca=finca))
        d.ir_a(Paso.FINCA_VEREDA)

    def _finca_vereda(self, d: Dialogo, texto: str) -> None:
        opciones = [(a, b) for a, b in d.ctx.get("opciones", [])]
        if normalizar(texto) in {OTRA, "otra vereda"}:
            d.decir(t.ESCRIBE_VEREDA)
            return
        elegida = elegir(texto, opciones) if normalizar(texto).isdigit() else None
        vereda = elegida[1] if elegida else " ".join(texto.split())
        if not 2 <= len(vereda) <= 100 or not re.search(r"[^\W\d_]", vereda):
            d.decir(t.VEREDA_INVALIDA)
            return
        guardadas = {normalizar_codigo(v): v for v in repo.veredas(d.s)}  # misma vereda escrita de otra forma
        d.ctx["nueva_finca"]["vereda"] = guardadas.get(normalizar_codigo(vereda), vereda)
        d.decir(PedirUbicacion(t.PEDIR_UBICACION_FINCA.format(finca=d.ctx["nueva_finca"]["nombre"])))
        d.ir_a(Paso.FINCA_UBICACION)

    def _finca_ubicacion(self, d: Dialogo, texto: str) -> None:
        nueva = d.ctx["nueva_finca"]
        punto = self._leer_ubicacion(d, t.PEDIR_UBICACION_FINCA.format(finca=nueva["nombre"]))
        if punto is None:
            return
        nueva.update(latitud=punto[0], longitud=punto[1])
        avisos = [] if dentro_de_zetaquira(*punto) else [t.AVISO_FUERA_DE_ZETAQUIRA]
        self._confirmar_ubicacion(d, punto, nueva["nombre"], f"Vereda {nueva['vereda']}", avisos)
        d.ir_a(Paso.FINCA_CONFIRMAR_UBICACION)

    def _finca_confirmar_ubicacion(self, d: Dialogo, texto: str) -> None:
        assert d.productor is not None
        if d.m.tipo == "location":  # mandó otra ubicación en vez de responder: se toma la nueva
            self._finca_ubicacion(d, texto)
            return
        nueva = d.ctx["nueva_finca"]
        aceptada = self._respuesta_ubicacion(d, texto, t.PEDIR_UBICACION_FINCA.format(finca=nueva["nombre"]))
        if aceptada is None:  # sigue esperando la confirmación
            return
        if not aceptada:
            d.ir_a(Paso.FINCA_UBICACION)
            return
        id_finca = repo.crear_finca(
            d.s, d.productor.id, nueva["nombre"], nueva["vereda"], nueva["latitud"], nueva["longitud"]
        )
        descripcion = f"Finca {id_finca} registrada por WhatsApp por el productor {d.productor.id}"
        repo.registrar_auditoria(d.s, "REGISTRO_FINCA", descripcion, "fincas")
        d.ctx.pop("nueva_finca")
        d.ctx.update(finca_id=id_finca, finca_nombre=nueva["nombre"])
        d.decir(t.FINCA_CREADA.format(nombre=nueva["nombre"]))
        self._pedir_cultivo(d)

    # --- Ubicación (finca y lote) ------------------------------------------------------------------

    @staticmethod
    def _leer_ubicacion(d: Dialogo, pedido: str) -> tuple[float, float] | None:
        """La ubicación compartida, o None tras volver a pedirla (otro tipo de mensaje o un punto imposible)."""
        if d.m.tipo != "location":
            d.decir(t.UBICACION_FALTA)
            d.decir(PedirUbicacion(pedido))
            return None
        try:
            punto = float(d.m.contenido["latitud"]), float(d.m.contenido["longitud"])
        except (KeyError, TypeError, ValueError):
            d.decir(PedirUbicacion(pedido))
            return None
        if not en_colombia(*punto):
            d.decir(t.UBICACION_FUERA_DE_COLOMBIA)
            d.decir(PedirUbicacion(pedido))
            return None
        return punto

    @staticmethod
    def _confirmar_ubicacion(
        d: Dialogo, punto: tuple[float, float], nombre: str, direccion: str, avisos: list[str]
    ) -> None:
        """Muestra el punto en el mapa y pide confirmarlo."""
        d.decir(Ubicacion(punto[0], punto[1], nombre, direccion))
        pregunta = " ".join([*avisos, t.CONFIRMAR_UBICACION.format(nombre=nombre)])
        d.decir(Botones(pregunta, (Opcion(SI, t.BOTON_ES_AQUI), Opcion(OTRA, t.BOTON_OTRA_UBICACION))))

    @staticmethod
    def _respuesta_ubicacion(d: Dialogo, texto: str, pedido: str) -> bool | None:
        """«Sí, es aquí» → True. «Enviar otra» o «no» → la pide de nuevo y False. Otra cosa → repite y None."""
        limpio = normalizar(texto)
        if limpio in {OTRA, "enviar otra"} or si_o_no(texto) is False:
            d.decir(t.OTRA_UBICACION)
            d.decir(PedirUbicacion(pedido))
            return False
        if si_o_no(texto) is True or limpio == "si es aqui":
            return True
        d.decir(Botones(t.SI_O_NO, (Opcion(SI, t.BOTON_ES_AQUI), Opcion(OTRA, t.BOTON_OTRA_UBICACION))))
        return None

    # --- Lote: elegir uno guardado o registrar uno nuevo -------------------------------------------

    def _pedir_cultivo(self, d: Dialogo) -> None:
        cultivos = repo.cultivos(d.s, d.ctx["finca_id"])
        if not cultivos:  # RQF30: el lote se registra desde WhatsApp
            d.decir(t.SIN_CULTIVOS.format(finca=d.ctx.get("finca_nombre", "")))
            self._pedir_nombre_cultivo(d)
            return
        opciones = [(c.id, c.nombre) for c in cultivos]
        d.ctx["opciones"] = [list(o) for o in opciones]
        d.decir(self._menu_cultivos(d, cultivos))
        d.ir_a(Paso.ELEGIR_CULTIVO)

    @staticmethod
    def _menu_cultivos(d: Dialogo, cultivos: list[repo.CultivoFila]) -> Mensaje:
        if len(cultivos) >= MAXIMO_FILAS:
            return t.ELEGIR_CULTIVO_TEXTO.format(lista=lista_numerada([(c.id, c.nombre) for c in cultivos]))
        filas = [
            Opcion(str(n), c.nombre, ", ".join(ESPECIES.a_nombre(e) for e in c.especies))
            for n, c in enumerate(cultivos, start=1)
        ]
        filas.append(Opcion(NUEVO, t.FILA_LOTE_NUEVO, t.DESCRIPCION_LOTE_NUEVO))
        texto = t.ELEGIR_CULTIVO.format(finca=d.ctx.get("finca_nombre", ""))
        return Lista(texto, t.VER_LOTES, tuple(filas), titulo_seccion="Lotes")

    @staticmethod
    def _fijar_cultivo(d: Dialogo, cultivo: repo.CultivoFila) -> None:
        d.ctx.update(cultivo_id=cultivo.id, cultivo_nombre=cultivo.nombre, especies=cultivo.especies)

    def _elegir_cultivo(self, d: Dialogo, texto: str) -> None:
        if normalizar(texto) in _NUEVA:
            self._pedir_nombre_cultivo(d)
            return
        cultivos = repo.cultivos(d.s, d.ctx["finca_id"])
        elegido = elegir(texto, [(c.id, c.nombre) for c in cultivos])
        if elegido is None:
            d.decir(t.OPCION_INVALIDA.format(ayuda=t.RESPONDE_CON_NUMERO))
            d.decir(self._menu_cultivos(d, cultivos))
            return
        self._fijar_cultivo(d, next(c for c in cultivos if c.id == elegido[0]))
        self._pedir_especie(d)

    @staticmethod
    def _pedir_nombre_cultivo(d: Dialogo) -> None:
        d.decir(t.PEDIR_NOMBRE_CULTIVO)
        d.ir_a(Paso.CULTIVO_NOMBRE)

    def _cultivo_nombre(self, d: Dialogo, texto: str) -> None:
        nombre = " ".join(texto.split())
        if not nombre or len(nombre) > 100 or repo.nombre_de_cultivo_en_uso(d.s, d.ctx["finca_id"], nombre):
            d.decir(t.NOMBRE_CULTIVO_INVALIDO)
            return
        d.ctx["nuevo_cultivo"] = {"nombre": nombre}
        d.decir(MENU_TIPO_CULTIVO)
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
            d.decir(MENU_TIPO_CULTIVO)
            return
        d.ctx["nuevo_cultivo"]["tipo"] = tipo.value
        d.decir(menu_especies_cultivo(d.ctx["nuevo_cultivo"]["nombre"]))
        d.ir_a(Paso.CULTIVO_ESPECIES)

    def _cultivo_especies(self, d: Dialogo, texto: str) -> None:
        todas = list(EspecieCultivo)
        numeros = {int(n) for n in re.findall(r"\d+", texto)}
        elegidas = [e for i, e in enumerate(todas, start=1) if i in numeros]
        if not elegidas:
            d.decir(t.ESPECIES_INVALIDAS)
            d.decir(menu_especies_cultivo(d.ctx["nuevo_cultivo"]["nombre"]))
            return
        nuevo = d.ctx["nuevo_cultivo"]
        nuevo["especies"] = [str(e) for e in elegidas]
        d.decir(PedirUbicacion(t.PEDIR_UBICACION_LOTE.format(lote=nuevo["nombre"])))
        d.ir_a(Paso.CULTIVO_UBICACION)

    def _cultivo_ubicacion(self, d: Dialogo, texto: str) -> None:
        nuevo = d.ctx["nuevo_cultivo"]
        punto = self._leer_ubicacion(d, t.PEDIR_UBICACION_LOTE.format(lote=nuevo["nombre"]))
        if punto is None:
            return
        nuevo.update(latitud=punto[0], longitud=punto[1])
        km = distancia_km(punto, repo.ubicacion_de_finca(d.s, d.ctx["finca_id"]))
        avisos = [t.AVISO_LOTE_LEJOS.format(km=f"{km:.1f}".replace(".", ","))] if km > DISTANCIA_LOTE_FINCA_KM else []
        self._confirmar_ubicacion(d, punto, nuevo["nombre"], f"Finca {d.ctx.get('finca_nombre', '')}", avisos)
        d.ir_a(Paso.CULTIVO_CONFIRMAR_UBICACION)

    def _cultivo_confirmar_ubicacion(self, d: Dialogo, texto: str) -> None:
        if d.m.tipo == "location":
            self._cultivo_ubicacion(d, texto)
            return
        nuevo = d.ctx["nuevo_cultivo"]
        aceptada = self._respuesta_ubicacion(d, texto, t.PEDIR_UBICACION_LOTE.format(lote=nuevo["nombre"]))
        if aceptada is None:  # sigue esperando la confirmación
            return
        if not aceptada:
            d.ir_a(Paso.CULTIVO_UBICACION)
            return
        id_cultivo = repo.crear_cultivo(
            d.s, d.ctx["finca_id"], nuevo["nombre"], nuevo["tipo"], nuevo["especies"],
            (nuevo["latitud"], nuevo["longitud"]),
        )  # fmt: skip
        descripcion = f"Lote {id_cultivo} registrado por WhatsApp en la finca {d.ctx['finca_id']}"
        repo.registrar_auditoria(d.s, "REGISTRO_CULTIVO", descripcion, "cultivos")
        d.ctx.pop("nuevo_cultivo")
        d.ctx.update(cultivo_id=id_cultivo, cultivo_nombre=nuevo["nombre"], especies=nuevo["especies"])
        d.decir(t.CULTIVO_CREADO.format(nombre=nuevo["nombre"]))
        self._pedir_especie(d)

    # --- Especie, afectación y zona ------------------------------------------------------------------

    def _pedir_especie(self, d: Dialogo) -> None:
        especies: list[str] = d.ctx["especies"]
        if len(especies) == 1:
            d.ctx["especie"] = especies[0]
            self._pedir_afectacion(d)
        else:
            opciones = [(e, ESPECIES.a_nombre(e)) for e in especies]
            d.ctx["opciones"] = [list(o) for o in opciones]
            d.decir(menu_especie(opciones))
            d.ir_a(Paso.ELEGIR_ESPECIE)

    def _elegir_especie(self, d: Dialogo, texto: str) -> None:
        opciones = [(a, b) for a, b in d.ctx.get("opciones", [])]
        elegida = elegir(texto, opciones)
        if elegida is None:
            d.decir(t.OPCION_INVALIDA.format(ayuda=t.RESPONDE_CON_NUMERO))
            d.decir(menu_especie(opciones))
            return
        d.ctx["especie"] = elegida[0]
        self._pedir_afectacion(d)

    @staticmethod
    def _pedir_afectacion(d: Dialogo) -> None:
        d.decir(MENU_AFECTACION)
        d.ir_a(Paso.AFECTACION)

    def _afectacion(self, d: Dialogo, texto: str) -> None:
        limpio = normalizar(texto)
        if limpio in {"1", "todo", "todo el cultivo", "todo el lote", "total"}:  # no se asume: lo confirma (RQF33)
            self._abrir_caso(d, t.ZONA_TOTAL)
        elif limpio in {"2", "una parte", "parte", "parcial"}:
            d.decir(t.PEDIR_ZONA)
            d.ir_a(Paso.ZONA)
        else:
            d.decir(t.OPCION_INVALIDA.format(ayuda=t.RESPONDE_CON_NUMERO))
            d.decir(MENU_AFECTACION)

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
            d.decir(con_listo(t.SOLO_FOTOS))

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
            d.decir(con_listo(t.LIMITE_POR_TIPO.format(por_tipo=MAXIMO_FOTOS_POR_TIPO)))
            return
        repo.guardar_foto(d.s, d.conv.caso_id, tipo, resultado.clave_s3, descripcion or None)
        total = sum(por_tipo.values()) + 1
        d.decir(con_listo(t.FOTO_ACEPTADA.format(n=total, total=MAXIMO_FOTOS_UTILES)))
        if total >= MAXIMO_FOTOS_UTILES:
            d.decir(t.LIMITE_TOTAL.format(total=MAXIMO_FOTOS_UTILES))
            self._resumen(d)

    def _resumen(self, d: Dialogo) -> None:
        assert d.conv.caso_id is not None
        fotos = sum(repo.fotos_por_tipo(d.s, d.conv.caso_id).values())
        resumen = t.RESUMEN.format(
            finca=d.ctx.get("finca_nombre", ""),
            cultivo=d.ctx.get("cultivo_nombre", ""),
            especie=ESPECIES.a_nombre(d.ctx["especie"]),
            zona=d.ctx.get("zona", ""),
            respuestas=len(repo.historial(d.s, d.conv.caso_id)),
            fotos=fotos,
        )
        d.decir(si_no(resumen, t.BOTON_ENVIAR, t.BOTON_SEGUIR))
        d.ir_a(Paso.CONFIRMAR_ENVIO)

    def _confirmar_envio(self, d: Dialogo, texto: str) -> None:
        respuesta = si_o_no(texto)
        if respuesta is None:
            d.decir(si_no(t.SI_O_NO, t.BOTON_ENVIAR, t.BOTON_SEGUIR))
        elif not respuesta:
            d.decir(con_listo(t.NO_ENVIADO))
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
        lideres = tuple(str(i) for (i,) in d.s.execute(LIDERES_ACTIVOS_SQL))
        d.ef.avisos.append(
            Aviso(
                lideres, CASO_SIN_TECNICO, "Caso nuevo sin técnico",
                f"{codigo_caso(d.conv.caso_id)} · {d.productor.nombre}", f"/casos/{d.conv.caso_id}",
            )
        )  # fmt: skip
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
        """Con una consulta abierta, confirma antes de borrarla. Sin consulta (p. ej., a mitad del registro de una
        finca), olvida lo que llevaba la conversación y empieza de nuevo de inmediato."""
        if not d.conv.caso_id:
            d.reiniciar()
            d.decir(t.REINICIADO)
            self._inicio(d, "")
            return
        d.ctx["volver_a"] = d.conv.paso
        d.decir(si_no(t.CONFIRMAR_SALIR))
        d.ir_a(Paso.CONFIRMAR_SALIR)

    def _confirmar_salir(self, d: Dialogo, texto: str) -> None:
        respuesta = si_o_no(texto)
        if respuesta is None:
            d.decir(si_no(t.SI_O_NO))
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
            d.decir(si_no(t.SI_O_NO))
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
