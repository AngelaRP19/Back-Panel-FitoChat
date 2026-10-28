"""Reglas de las alertas del panel (guía §11, módulo 6).

Flujo: GENERADA → (EN_REVISION) → CONFIRMADA o DESCARTADA → COMUNICADA → CORREGIDA. Solo el líder confirma, descarta,
comunica y corrige; el técnico consulta y puede marcar «seguir». Mientras no se defina quién marca «En revisión»
(P-06), una alerta GENERADA admite confirmar y descartar directamente.

Los envíos son **reanudables y sin duplicados**: cada destinatario es una fila (`alerta_destinatarios`) que un
proceso «reclama» antes de enviar y cierra después, con commit por lote. Si el proceso se cae a la mitad, el
reintento sigue con los pendientes y no reenvía a quien ya recibió. La alerta pasa a COMUNICADA (o CORREGIDA) solo
cuando se intentó con todos.
"""

import asyncio
import logging
from datetime import date

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.api.paginacion import ParametrosPagina
from app.core.catalogos_api import CATEGORIAS, EstadoAlerta, normalizar_codigo
from app.core.codigos import codigo_alerta, codigo_caso
from app.core.config import get_settings
from app.core.roles import Rol
from app.core.security_cognito import UsuarioActual
from app.core.telefonos import celular_de_e164
from app.infrastructure.whatsapp_client import FALLIDO, ResultadoEnvio, WhatsAppClient
from app.modules.panel_api.alertas import repository as repo
from app.modules.panel_api.alertas.repository import AlertaFila, DestinoFila
from app.modules.panel_api.alertas.schemas import (
    AlertaDetalle,
    AlertaResumen,
    Borrador,
    CasoAlerta,
    ComunicarIn,
    ConteosAlertas,
    Correccion,
    CorregirIn,
    Descarte,
    DestinatariosVereda,
    PaginaAlertas,
    ParametroCategoria,
    ParametrosAlerta,
    ParametrosIn,
    ProductorDestino,
)
from app.modules.panel_api.auditoria import auditar
from app.modules.panel_api.casos.schemas import PersonaRef
from app.modules.panel_api.notificaciones import service as avisos
from app.modules.panel_api.notificaciones.service import Notificador

logger = logging.getLogger(__name__)

E = EstadoAlerta
DECISION_ABIERTA = (E.GENERADA.value, E.EN_REVISION.value)

# Plantillas PROVISIONALES: el equipo debe validar el texto exacto (P-B12). La recomendación nunca es un tratamiento.
PLANTILLA_ALERTA = (
    "Aviso fitosanitario ANUC Zetaquirá: se detectaron varios casos de {categoria} cerca de su finca. {mensaje}"
)
PLANTILLA_CORRECCION = (
    "Aviso fitosanitario: el mensaje de alerta sobre {categoria} enviado el {fecha} no aplica. "
    "Por favor, haga caso omiso de ese mensaje. {mensaje}"
)
NOTA_NO_TRANSMISIBLE = "Esta categoría no se transmite entre cultivos, por eso no participa en las alertas."


def alerta_no_encontrada() -> ApiError:
    return ApiError(404, "NO_ENCONTRADO", "La alerta no existe.")


def transicion_invalida(mensaje: str) -> ApiError:
    return ApiError(409, "TRANSICION_INVALIDA", mensaje)


def _persona(id_: str | None, nombre: str | None) -> PersonaRef | None:
    return PersonaRef(id=id_, nombre=nombre or "") if id_ else None


class AlertasService:
    def __init__(self, session: AsyncSession, whatsapp: WhatsAppClient, notificador: Notificador | None = None) -> None:
        self.session = session
        self.whatsapp = whatsapp
        self.notificador = notificador or Notificador()

    async def _avisar(self, id_alerta: str, tipo: str, titulo: str) -> None:
        """B-10: después del commit. Líderes activos y técnicos que siguen la alerta."""
        f = await self._o_404(id_alerta)
        destinatarios = [
            *await avisos.lideres_activos(self.session),
            *await avisos.seguidores_de_alerta(self.session, id_alerta),
        ]
        texto = f"{codigo_alerta(id_alerta)} · {f.nombre_categoria}" + (f" · {f.vereda}" if f.vereda else "")
        await self.notificador.avisar(destinatarios, tipo, titulo, texto, f"/alertas/{id_alerta}")

    # --- Lectura ---------------------------------------------------------------------------------------------------

    @staticmethod
    def a_resumen(f: AlertaFila) -> AlertaResumen:
        estado = EstadoAlerta(f.estado)
        envios = estado in (E.COMUNICADA, E.CORREGIDA) or f.notificados > 0 or f.fallidos > 0
        return AlertaResumen(
            id=f.id,
            codigo=codigo_alerta(f.id),
            estado=estado,
            categoria=CATEGORIAS.a_codigo(f.nombre_categoria),
            vereda=normalizar_codigo(f.vereda) if f.vereda else None,
            generada_en=f.generada_en,
            num_casos=f.num_casos,
            radio_km=round(f.radio_km, 2),
            parametros=ParametrosAlerta(
                num_casos=f.num_minimo, distancia_km=float(f.distancia_km), intervalo_dias=f.intervalo_dias
            ),
            revisa=None,  # ninguna acción lleva la alerta a EN_REVISION todavía (P-06)
            confirmada_por=(
                _persona(f.id_lider_gestion, f.nombre_lider_gestion)
                if estado in (E.CONFIRMADA, E.COMUNICADA, E.CORREGIDA)
                else None
            ),
            productores_notificados=f.notificados if envios else None,
            envios_fallidos=f.fallidos if envios else None,
            motivo_descarte=f.justificacion_descarte if estado is E.DESCARTADA else None,
        )

    async def _o_404(self, id_alerta: str) -> AlertaFila:
        fila = await repo.obtener(self.session, id_alerta)
        if fila is None:
            raise alerta_no_encontrada()
        return fila

    async def listar(self, estado: EstadoAlerta | None, pagina: ParametrosPagina) -> PaginaAlertas:
        filas, total, conteos = await repo.listar(self.session, pagina, estado.value if estado else None)
        return PaginaAlertas(
            items=[self.a_resumen(f) for f in filas],
            total=total,
            page=pagina.page,
            page_size=pagina.page_size,
            conteos=ConteosAlertas(
                TODAS=sum(conteos.values()), **{k: v for k, v in conteos.items() if k in ConteosAlertas.model_fields}
            ),
        )

    async def detalle(self, actor: UsuarioActual, id_alerta: str) -> AlertaDetalle:
        f = await self._o_404(id_alerta)
        resumen = self.a_resumen(f)
        es_lider = actor.rol is Rol.LIDER_TECNICO
        casos = []
        for c in await repo.casos(self.session, id_alerta):
            enlazable = es_lider or c.id_tecnico == actor.id
            casos.append(
                CasoAlerta(
                    id=c.id,
                    codigo=codigo_caso(c.id),
                    # El técnico no ve el productor de casos que no tiene asignados (P-07).
                    productor_nombre=c.nombre_productor if enlazable else None,
                    finca_nombre=c.nombre_finca,
                    resultado_tecnico=c.resultado_tecnico,
                    enviado_en=c.enviado_en,
                    latitud=None if c.latitud is None else float(c.latitud),
                    longitud=None if c.longitud is None else float(c.longitud),
                    enlazable=enlazable,
                )
            )
        estado = resumen.estado
        decision = _persona(f.id_lider_gestion, f.nombre_lider_gestion)
        pendientes = await repo.sin_diagnostico(self.session, id_alerta, f.distancia_km)
        return AlertaDetalle(
            **resumen.model_dump(),
            casos=casos,
            sin_diagnostico=[codigo_caso(c) for c in pendientes],
            confirmada_en=f.fecha_confirmacion if estado is not E.DESCARTADA else None,
            comunicada_en=f.comunicada_el,
            descarte=Descarte(motivo=f.justificacion_descarte or "", por=decision, en=f.fecha_confirmacion)
            if estado is E.DESCARTADA
            else None,
            # Las veredas y la fecha del borrador no se guardan (pendiente de modelo): se propone la de la alerta.
            borrador=Borrador(
                mensaje=f.recomendacion, veredas=[resumen.vereda] if resumen.vereda else [], guardado_en=None
            )
            if estado is E.CONFIRMADA and f.recomendacion
            else None,
            mensaje_enviado=f.mensaje_enviado,
            correccion=Correccion(
                mensaje=f.mensaje_correccion,
                por=_persona(f.id_lider_correccion, f.nombre_lider_correccion),
                en=f.fecha_correccion,
            )
            if f.mensaje_correccion
            else None,
            siguiendo=False if es_lider else await repo.esta_siguiendo(self.session, id_alerta, actor.id),
        )

    async def destinatarios(self, id_alerta: str, vereda: str | None) -> list[DestinatariosVereda]:
        """Productores a `distancia_km` o menos de las fincas de los casos, agrupados por vereda."""
        f = await self._o_404(id_alerta)
        filtro = normalizar_codigo(vereda) if vereda else None
        grupos: dict[str, dict[str, DestinoFila]] = {}
        nombres: dict[str, str] = {}
        zona: set[str] = set()
        for d in await repo.destinatarios(self.session, id_alerta, f.distancia_km):
            codigo = normalizar_codigo(d.vereda)
            if filtro and codigo != filtro:
                continue
            grupos.setdefault(codigo, {})[d.id_productor] = d
            nombres[codigo] = d.vereda
            if d.zona_alerta:
                zona.add(codigo)
        return [
            DestinatariosVereda(
                vereda=codigo,
                nombre=nombres[codigo],
                zona_alerta=codigo in zona,
                deshabilitados=sum(not d.activo for d in grupo.values()),
                productores=[
                    ProductorDestino(id=d.id_productor, nombre=d.nombre, celular=celular_de_e164(d.telefono))
                    for d in grupo.values()
                    if d.activo
                ],
            )
            for codigo, grupo in sorted(grupos.items(), key=lambda kv: (kv[0] not in zona, nombres[kv[0]]))
        ]

    # --- Decisiones ------------------------------------------------------------------------------------------------

    async def _bloquear(self, id_alerta: str) -> str:
        estado = await repo.bloquear(self.session, id_alerta)
        if estado is None:
            await self.session.rollback()
            raise alerta_no_encontrada()
        return estado

    async def confirmar(self, actor: UsuarioActual, id_alerta: str) -> AlertaDetalle:
        estado = await self._bloquear(id_alerta)
        if estado not in DECISION_ABIERTA:
            await self.session.rollback()
            raise transicion_invalida("La alerta ya no está pendiente de decisión.")
        await repo.decidir(self.session, id_alerta, E.CONFIRMADA.value, actor.id)
        await auditar(
            self.session, actor, "CONFIRMAR_ALERTA", "alertas", f"Alerta {id_alerta} confirmada",
            anterior={"estado": estado}, nuevo={"estado": E.CONFIRMADA.value},
        )  # fmt: skip
        await self.session.commit()
        await self._avisar(id_alerta, avisos.ALERTA_CONFIRMADA, "Alerta confirmada")
        return await self.detalle(actor, id_alerta)

    async def descartar(self, actor: UsuarioActual, id_alerta: str, justificacion: str) -> AlertaDetalle:
        estado = await self._bloquear(id_alerta)
        if estado not in DECISION_ABIERTA:
            await self.session.rollback()
            raise transicion_invalida("La alerta ya no está pendiente de decisión.")
        await repo.decidir(self.session, id_alerta, E.DESCARTADA.value, actor.id, justificacion)
        await auditar(
            self.session, actor, "DESCARTAR_ALERTA", "alertas", f"Alerta {id_alerta} descartada",
            anterior={"estado": estado}, nuevo={"estado": E.DESCARTADA.value, "justificacion": justificacion},
        )  # fmt: skip
        await self.session.commit()
        return await self.detalle(actor, id_alerta)

    async def guardar_borrador(self, actor: UsuarioActual, id_alerta: str, mensaje: str) -> AlertaDetalle:
        estado = await self._bloquear(id_alerta)
        if estado != E.CONFIRMADA.value:
            await self.session.rollback()
            raise transicion_invalida("El mensaje se redacta con la alerta confirmada y aún sin comunicar.")
        await repo.guardar_borrador(self.session, id_alerta, mensaje)
        await self.session.commit()
        return await self.detalle(actor, id_alerta)

    # --- Envíos ----------------------------------------------------------------------------------------------------

    async def _enviar(self, id_alerta: str, tipo: str, texto: str) -> None:
        """Envía a los pendientes en lotes, con commit por lote y pausa entre lotes (límites de la API de Meta)."""
        ajustes = get_settings()
        while True:
            lote = await repo.reclamar_envios(self.session, id_alerta, tipo, ajustes.alertas_lote_envio)
            await self.session.commit()  # el reclamo queda firme antes de enviar: otro proceso no repite estos
            if not lote:
                return
            for posicion, envio in enumerate(lote):
                try:
                    try:
                        resultado = await run_in_threadpool(self.whatsapp.enviar_texto, envio.telefono, texto)
                    except Exception as error:  # un envío fallido no detiene al resto
                        logger.exception("Falló el envío de la alerta %s", id_alerta)
                        resultado = ResultadoEnvio(FALLIDO, error=type(error).__name__)
                    error_envio = (resultado.error or "")[:500] or None
                    await repo.registrar_resultado(self.session, envio.id, resultado.estado, error_envio)
                    await self.session.commit()  # cada resultado queda firme: una caída no borra lo ya enviado
                except BaseException:
                    # El envío en curso queda «en curso» (puede haber salido: no se repite hasta que venza la espera);
                    # los que ni se intentaron vuelven a pendientes para que el reintento los tome.
                    await self.session.rollback()
                    await repo.liberar_envios(self.session, [e.id for e in lote[posicion + 1 :]])
                    await self.session.commit()
                    raise
            if len(lote) >= ajustes.alertas_lote_envio and ajustes.alertas_pausa_lote_segundos > 0:
                await asyncio.sleep(ajustes.alertas_pausa_lote_segundos)

    async def comunicar(self, actor: UsuarioActual, id_alerta: str, datos: ComunicarIn) -> AlertaDetalle:
        """CONFIRMADA → COMUNICADA. Reintento seguro: no reenvía a quien ya recibió.

        Con la alerta ya COMUNICADA solo se admite si quedaron envíos fallidos: se reintentan únicamente esos.
        """
        estado = await self._bloquear(id_alerta)
        fila = await self._o_404(id_alerta)
        if estado == E.COMUNICADA.value and fila.fallidos == 0:
            await self.session.rollback()
            raise transicion_invalida("La alerta ya se comunicó.")
        if estado not in (E.CONFIRMADA.value, E.COMUNICADA.value):
            await self.session.rollback()
            raise transicion_invalida("Solo se comunica una alerta confirmada.")
        if estado == E.CONFIRMADA.value:
            destinos = [
                d
                for d in await repo.destinatarios(self.session, id_alerta, fila.distancia_km)
                if d.activo and normalizar_codigo(d.vereda) in datos.veredas
            ]
            if not destinos:
                await self.session.rollback()
                raise ApiError(
                    422, "VALIDACION", "Revisa los campos marcados.",
                    {"veredas": "Elige al menos una vereda con productores activos."},
                )  # fmt: skip
            if fila.mensaje_enviado is None:  # primera vez; si se reanuda, se conserva el texto original
                await repo.fijar_mensaje(
                    self.session,
                    id_alerta,
                    datos.mensaje,
                    PLANTILLA_ALERTA.format(categoria=fila.nombre_categoria, mensaje=datos.mensaje),
                )
            await repo.asegurar_envios(self.session, id_alerta, repo.ALERTA, sorted({d.id_productor for d in destinos}))
        await repo.reintentar_fallidos(self.session, id_alerta, repo.ALERTA)
        texto = (await self._o_404(id_alerta)).mensaje_enviado or ""
        await self.session.commit()

        await self._enviar(id_alerta, repo.ALERTA, texto)

        if await self._bloquear(id_alerta) == E.CONFIRMADA.value and not await repo.envios_sin_cerrar(
            self.session, id_alerta, repo.ALERTA
        ):
            await repo.marcar_comunicada(self.session, id_alerta)
            cuenta = await repo.contar_envios(self.session, id_alerta, repo.ALERTA)
            await auditar(
                self.session, actor, "COMUNICAR_ALERTA", "alertas",
                f"Alerta {id_alerta} comunicada: {sum(cuenta.get(e, 0) for e in repo.ENTREGADOS)} entregados, "
                f"{cuenta.get(repo.FALLIDO, 0)} fallidos",
                nuevo={"estado": E.COMUNICADA.value, "envios": cuenta},
            )  # fmt: skip
            await self.session.commit()
            await self._avisar(id_alerta, avisos.ALERTA_COMUNICADA, "Alerta comunicada")
        await self.session.commit()
        return await self.detalle(actor, id_alerta)

    async def corregir(self, actor: UsuarioActual, id_alerta: str, datos: CorregirIn) -> AlertaDetalle:
        """COMUNICADA → CORREGIDA, una sola vez, a quienes recibieron la alerta original (D-17). Reanudable."""
        estado = await self._bloquear(id_alerta)
        fila = await self._o_404(id_alerta)
        if estado == E.CORREGIDA.value:
            await self.session.rollback()
            raise transicion_invalida("La alerta ya fue corregida.")
        if estado != E.COMUNICADA.value:
            await self.session.rollback()
            raise transicion_invalida("Solo se corrige una alerta que ya se comunicó.")
        if fila.mensaje_correccion is None:  # primera vez; si se reanuda, se conserva la explicación original
            receptores = await repo.productores_que_recibieron(self.session, id_alerta)
            if not receptores:
                await self.session.rollback()
                raise transicion_invalida("Nadie recibió la alerta, no hay a quién enviar la corrección.")
            await repo.fijar_correccion(self.session, id_alerta, datos.mensaje, actor.id)
            await repo.asegurar_envios(self.session, id_alerta, repo.CORRECCION, receptores)
        await repo.reintentar_fallidos(self.session, id_alerta, repo.CORRECCION)
        fila = await self._o_404(id_alerta)
        texto = PLANTILLA_CORRECCION.format(
            categoria=fila.nombre_categoria,
            fecha=(fila.comunicada_el or date.today()).strftime("%d/%m/%Y"),
            mensaje=fila.mensaje_correccion or datos.mensaje,
        )
        await self.session.commit()

        await self._enviar(id_alerta, repo.CORRECCION, texto)

        if await self._bloquear(id_alerta) == E.COMUNICADA.value and not await repo.envios_sin_cerrar(
            self.session, id_alerta, repo.CORRECCION
        ):
            await repo.marcar_corregida(self.session, id_alerta)
            cuenta = await repo.contar_envios(self.session, id_alerta, repo.CORRECCION)
            await auditar(
                self.session, actor, "CORREGIR_ALERTA", "alertas",
                f"Alerta {id_alerta} corregida: {sum(cuenta.get(e, 0) for e in repo.ENTREGADOS)} entregados, "
                f"{cuenta.get(repo.FALLIDO, 0)} fallidos",
                nuevo={"estado": E.CORREGIDA.value, "envios": cuenta},
            )  # fmt: skip
            await self.session.commit()
            await self._avisar(id_alerta, avisos.ALERTA_CORREGIDA, "Alerta corregida")
        await self.session.commit()
        return await self.detalle(actor, id_alerta)

    # --- Seguir (P-06: el efecto está por definir) -------------------------------------------------------------------

    async def seguir(self, actor: UsuarioActual, id_alerta: str, seguir: bool) -> bool:
        await self._o_404(id_alerta)
        if await repo.esta_siguiendo(self.session, id_alerta, actor.id) != seguir:
            await auditar(
                self.session, actor, "SEGUIR_ALERTA" if seguir else "DEJAR_SEGUIR", "alertas",
                f"Alerta {id_alerta} {'seguida' if seguir else 'ya no se sigue'}",
            )  # fmt: skip
            await self.session.commit()
        return seguir

    # --- Parámetros epidemiológicos ----------------------------------------------------------------------------------

    async def parametros(self) -> list[ParametroCategoria]:
        por_nombre = {p.nombre_categoria: p for p in await repo.parametros(self.session)}
        ediciones = await repo.ultimas_ediciones(self.session)
        salida = []
        for codigo in CATEGORIAS.codigos:
            p = por_nombre.get(CATEGORIAS.a_nombre(codigo))
            if p is None:
                continue
            editor = ediciones.get(codigo)
            salida.append(
                ParametroCategoria(
                    categoria=codigo,
                    nombre=p.nombre_categoria,
                    habilitada=p.transmisible,
                    num_casos=p.num_casos,
                    distancia_km=None if p.distancia_km is None else float(p.distancia_km),
                    intervalo_dias=p.intervalo_dias,
                    nota=None if p.transmisible else NOTA_NO_TRANSMISIBLE,
                    actualizado_por=PersonaRef(id=editor[0], nombre=editor[1]) if editor else None,
                    actualizado_en=p.actualizado_en,
                )
            )
        return salida

    async def guardar_parametros(self, actor: UsuarioActual, codigo: str, datos: ParametrosIn) -> ParametroCategoria:
        actuales = {p.nombre_categoria: p for p in await repo.parametros(self.session)}
        actual = actuales.get(CATEGORIAS.a_nombre(codigo))
        if actual is None:
            raise ApiError(404, "NO_ENCONTRADO", "La categoría no existe.")
        if not actual.transmisible:
            raise ApiError(422, "VALIDACION", "Revisa los campos marcados.", {"categoria": NOTA_NO_TRANSMISIBLE})
        await repo.guardar_parametros(
            self.session, actual.id_categoria, datos.num_casos, datos.distancia_km, datos.intervalo_dias
        )
        await auditar(
            self.session, actor, "EDITAR_PARAMETROS", "parametros_epidemiologicos",
            f"Parámetros de la categoría {codigo}",
            anterior={
                "num_casos": actual.num_casos,
                "distancia_km": float(actual.distancia_km) if actual.distancia_km is not None else None,
                "intervalo_dias": actual.intervalo_dias,
            },
            nuevo=datos.model_dump(),
        )  # fmt: skip
        await self.session.commit()
        return next(p for p in await self.parametros() if p.categoria == codigo)
