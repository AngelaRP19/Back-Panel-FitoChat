"""Avisos al productor por WhatsApp cuando su caso cambia en el panel: técnico asignado, informe del diagnóstico y
cierre.

Se envían **después** de confirmar el cambio y nunca hacen fallar la acción del panel: si WhatsApp no responde, queda
registrado. Cada aviso deja su rastro en la auditoría (`AVISO_PRODUCTOR`): qué se avisó, de qué caso y con qué
resultado (ENVIADO, SIMULADO o FALLIDO con el código de Meta), sin el teléfono ni el texto.

Meta solo acepta texto libre dentro de las 24 h siguientes al último mensaje del productor: fuera de esa ventana
acepta el envío y después lo descarta (código 131047), sin que el envío falle. Por eso, antes de enviar se mira cuándo
escribió el productor por última vez: dentro de la ventana el aviso sale ya; fuera, queda PENDIENTE y el chatbot lo
entrega en cuanto el productor vuelva a escribir (`notificaciones.service.avisos_pendientes`). Con plantillas
aprobadas por Meta (Fase C) podría salir de inmediato también fuera de la ventana.

El informe incluye lo que escribió el técnico (descripción y observaciones): es su diagnóstico, no una respuesta de la
IA, así que no pasa por el guardrail de tratamientos.
"""

import logging

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.catalogos_api import CATEGORIAS
from app.core.codigos import codigo_caso
from app.core.logging import mask_phone
from app.core.security_cognito import UsuarioActual
from app.infrastructure.notificaciones_store import NotificacionesStore, get_notificaciones_store
from app.infrastructure.whatsapp_client import WhatsAppClient
from app.modules.panel_api.auditoria import auditar
from app.modules.panel_api.casos.repository import CasoFila, ventana_abierta
from app.modules.panel_api.notificaciones.service import guardar_aviso_pendiente

logger = logging.getLogger(__name__)

ASIGNADO = (
    "Hola {nombre}, tu consulta {codigo} ({finca} · {lote}) ya tiene técnico: {tecnico} la está revisando. "
    "Te escribiré cuando registre el diagnóstico."
)
REASIGNADO = "Hola {nombre}, tu consulta {codigo} ahora la revisa el técnico {tecnico}."
INFORME = (
    "Informe del técnico sobre tu consulta {codigo}\n- Finca: {finca}\n- Lote: {lote} ({especie})\n"
    "- Resultado: {resultado}"
)
CATEGORIA = "\n- Problema identificado: {categoria}"
DESCRIPCION = "\n- Descripción: {texto}"
OBSERVACIONES = "\n- Observaciones del técnico: {texto}"
VISITA = "\n- Visita a la finca: {texto}"
FIRMA = "\nTécnico: {tecnico}. Si tienes dudas, comunícate con la ANUC."
CERRADO = (
    "Hola {nombre}, tu consulta {codigo} quedó cerrada. Gracias por usar el asistente de la ANUC. Si aparece un "
    "problema nuevo, escríbeme."
)
RESULTADO = {
    "CONFIRMADO": "el técnico confirmó la identificación preliminar",
    "OTRA_PATOLOGIA": "el técnico identificó otro problema",
    "NO_IDENTIFICADO": "el técnico no pudo identificar el problema con lo enviado",
    "REQUIERE_VISITA": "el técnico necesita visitar la finca",
}


def _primer_nombre(nombre: str) -> str:
    return nombre.split()[0] if nombre.split() else nombre


def texto_asignado(fila: CasoFila, tecnico: str, reasignado: bool) -> str:
    plantilla = REASIGNADO if reasignado else ASIGNADO
    return plantilla.format(
        nombre=_primer_nombre(fila.nombre_productor),
        codigo=codigo_caso(fila.id_caso),
        finca=fila.nombre_finca,
        lote=fila.nombre_cultivo,
        tecnico=tecnico,
    )


def texto_informe(
    fila: CasoFila,
    tecnico: str,
    resultado: str,
    categoria: str | None,
    descripcion: str | None,
    observaciones: str | None,
    motivo_visita: str | None,
) -> str:
    texto = INFORME.format(
        codigo=codigo_caso(fila.id_caso),
        finca=fila.nombre_finca,
        lote=fila.nombre_cultivo,
        especie=fila.nombre_especie,
        resultado=RESULTADO.get(resultado, resultado),
    )
    if categoria:
        texto += CATEGORIA.format(categoria=CATEGORIAS.a_nombre(categoria))
    if descripcion:
        texto += DESCRIPCION.format(texto=descripcion)
    if observaciones:
        texto += OBSERVACIONES.format(texto=observaciones)
    if motivo_visita:
        texto += VISITA.format(texto=motivo_visita)
    return texto + FIRMA.format(tecnico=tecnico)


def texto_cerrado(fila: CasoFila) -> str:
    return CERRADO.format(nombre=_primer_nombre(fila.nombre_productor), codigo=codigo_caso(fila.id_caso))


class AvisosProductor:
    """Envía el aviso y lo audita. Sin cliente de WhatsApp (p. ej., mal configurado) no hace nada."""

    def __init__(
        self, session: AsyncSession, whatsapp: WhatsAppClient | None, pendientes: NotificacionesStore | None = None
    ) -> None:
        self.session = session
        self.whatsapp = whatsapp
        self.pendientes = pendientes

    async def enviar(self, actor: UsuarioActual, fila: CasoFila, tipo: str, texto: str) -> None:
        if self.whatsapp is None:
            return
        datos = {"id_caso": fila.id_caso, "tipo": tipo, "celular": mask_phone(fila.telefono_productor)}
        try:
            if await ventana_abierta(self.session, fila.telefono_productor):
                estado = await self._enviar_ya(fila, texto, datos)
            else:
                store = self.pendientes or get_notificaciones_store()
                await run_in_threadpool(guardar_aviso_pendiente, store, fila.id_productor, tipo, texto)
                logger.info("Aviso al productor pendiente: fuera de la ventana de 24 h", extra=datos)
                estado = "PENDIENTE (fuera de la ventana de 24 h de WhatsApp: se entrega cuando el productor escriba)"
        except Exception:  # noqa: BLE001  (el aviso nunca tumba la acción del panel)
            logger.exception("No se pudo avisar al productor", extra=datos)
            return
        try:
            await auditar(
                self.session,
                actor,
                "AVISO_PRODUCTOR",
                "casos",
                f"Aviso {tipo} al productor {fila.id_productor} del caso {fila.id_caso}: {estado}",
            )
            await self.session.commit()
        except Exception:  # noqa: BLE001
            await self.session.rollback()
            logger.exception("No se pudo auditar el aviso al productor", extra={"id_caso": fila.id_caso})

    async def _enviar_ya(self, fila: CasoFila, texto: str, datos: dict[str, str]) -> str:
        """Envía el aviso y devuelve lo que queda en la auditoría: estado, wamid de Meta o el error."""
        assert self.whatsapp is not None
        resultado = await run_in_threadpool(self.whatsapp.enviar_texto, fila.telefono_productor, texto)
        if resultado.exito:
            logger.info("Aviso al productor enviado", extra={**datos, "wamid": resultado.id_externo})
        else:
            logger.warning("WhatsApp rechazó el aviso al productor", extra={**datos, "detalle": resultado.error})
        extra = resultado.error or (f"wamid {resultado.id_externo}" if resultado.id_externo else "")
        return f"{resultado.estado} ({extra})" if extra else resultado.estado
