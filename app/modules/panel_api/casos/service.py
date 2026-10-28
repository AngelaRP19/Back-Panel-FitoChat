"""Reglas de los casos del panel (guía §11, módulo 4): bandeja, detalle y asignación.

- El líder ve todos los casos; el técnico, solo los que tiene asignados (el alcance se aplica en SQL).
- Los permisos `puede_*` se calculan aquí, según rol, asignación y estado: la interfaz se guía por ellos.
- Asignar valida que el técnico esté activo, cierra la asignación vigente, crea la nueva y mueve el estado, todo en
  una transacción. El evento de la línea de tiempo lo anota el trigger (asignar) o el servicio (reasignar).
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.api.paginacion import ParametrosPagina
from app.core.catalogos_api import (
    CATEGORIAS,
    ESPECIES,
    ESTADOS_CASO,
    EstadoCaso,
    ResultadoAnalisis,
    ResultadoTecnico,
    normalizar_codigo,
)
from app.core.codigos import codigo_caso
from app.core.config import get_settings
from app.core.roles import Rol
from app.core.security_cognito import UsuarioActual
from app.core.state_machine import es_asignable, validar_transicion
from app.core.telefonos import celular_de_e164
from app.infrastructure.db import procedimientos as sp
from app.infrastructure.s3_storage_client import VIGENCIA_URL_SEGUNDOS, S3StorageClient
from app.modules.panel_api.auditoria import auditar
from app.modules.panel_api.casos import repository as repo
from app.modules.panel_api.casos.repository import Alcance, CasoFila, FiltrosCasos
from app.modules.panel_api.casos.schemas import (
    Alternativa,
    CasoDetalle,
    CasoResumen,
    CoincidenciaTexto,
    ConteosCasos,
    CuestionarioOut,
    CuestionarioResumen,
    CultivoCaso,
    CultivoDetalle,
    DescartaOut,
    DiagnosticoIn,
    DiagnosticoOut,
    EventoOut,
    EvidenciaOut,
    EvidenciasResumen,
    ExplicacionOut,
    FincaCaso,
    FincaDetalle,
    HistorialUltimo,
    PersonaRef,
    PorcentajeCategoria,
    Preliminar,
    ProductorCaso,
    ProductorDetalle,
    ResponsableEvento,
    RespuestaOut,
)

MAXIMO_FOTOS_UTILES = 12  # guía §2: ≤ 12 útiles por caso

TITULOS_EVENTO = {
    "CASO_CREADO": "Caso creado",
    "ANALISIS_CHATBOT": "Análisis del chatbot",
    "ENVIADO_A_REVISION": "Enviado al panel",
    "TECNICO_ASIGNADO": "Técnico asignado",
    "TECNICO_REASIGNADO": "Técnico reasignado",
    "DIAGNOSTICO_REGISTRADO": "Diagnóstico registrado",
    "CASO_CERRADO": "Caso cerrado",
    "CASO_DESCARTADO": "Caso descartado",
}


# Pasos que faltan según el estado: (tipo de evento, título, detalle).
_ASIGNACION = ("TECNICO_ASIGNADO", "Técnico asignado", "Pendiente · el líder técnico asigna un técnico")
_DIAGNOSTICO = ("DIAGNOSTICO_REGISTRADO", "Diagnóstico técnico", "Pendiente")
_CIERRE = ("CASO_CERRADO", "Cierre del caso", "Pendiente · define la fecha de vencimiento de conservación")
PASOS_PENDIENTES: dict[EstadoCaso, tuple[tuple[str, str, str], ...]] = {
    EstadoCaso.CREADO: (_ASIGNACION, _DIAGNOSTICO, _CIERRE),
    EstadoCaso.EN_ANALISIS: (_ASIGNACION, _DIAGNOSTICO, _CIERRE),
    EstadoCaso.PENDIENTE_REVISION: (_ASIGNACION, _DIAGNOSTICO, _CIERRE),
    EstadoCaso.EN_REVISION: (_DIAGNOSTICO, _CIERRE),
    EstadoCaso.REGISTRADO: (_CIERRE,),
}


def _pasos_pendientes(estado: EstadoCaso) -> list[EventoOut]:
    return [
        EventoOut(tipo=tipo, titulo=titulo, en=None, detalle=detalle, responsable=None, pendiente=True)
        for tipo, titulo, detalle in PASOS_PENDIENTES.get(estado, ())
    ]


def alcance_de(actor: UsuarioActual) -> Alcance:
    return Alcance(id_usuario=actor.id, es_lider=actor.rol is Rol.LIDER_TECNICO)


def no_encontrado() -> ApiError:
    return ApiError(404, "NO_ENCONTRADO", "El caso no existe.")


def _estado(fila: CasoFila) -> EstadoCaso:
    return EstadoCaso(ESTADOS_CASO.a_codigo(fila.nombre_estado))


def _preliminar(fila: CasoFila) -> Preliminar | None:
    if fila.id_identificacion_preliminar is None or fila.resultado_analisis is None:
        return None
    resultado = ResultadoAnalisis(fila.resultado_analisis)
    if resultado is ResultadoAnalisis.NO_IDENTIFICADO or fila.nombre_categoria_principal is None:
        return Preliminar(resultado=resultado, categoria=None, porcentaje=None)
    alternativa = None
    if resultado is ResultadoAnalisis.AMBIGUO and fila.nombre_alternativa and fila.porcentaje_alternativa is not None:
        alternativa = Alternativa(
            categoria=CATEGORIAS.a_codigo(fila.nombre_alternativa), porcentaje=float(fila.porcentaje_alternativa)
        )
    return Preliminar(
        resultado=resultado,
        categoria=CATEGORIAS.a_codigo(fila.nombre_categoria_principal),
        porcentaje=None if fila.porcentaje_principal is None else float(fila.porcentaje_principal),
        alternativa=alternativa,
    )


def _tecnico(fila: CasoFila) -> PersonaRef | None:
    return PersonaRef(id=fila.id_tecnico, nombre=fila.nombre_tecnico or "") if fila.id_tecnico else None


def a_resumen(fila: CasoFila) -> CasoResumen:
    return CasoResumen(
        id=fila.id_caso,
        codigo=codigo_caso(fila.id_caso),
        estado=_estado(fila),
        productor=ProductorCaso(
            id=fila.id_productor, nombre=fila.nombre_productor, celular=celular_de_e164(fila.telefono_productor)
        ),
        finca=FincaCaso(id=fila.id_finca, nombre=fila.nombre_finca, vereda=normalizar_codigo(fila.vereda_sector_finca)),
        cultivo=CultivoCaso(id=fila.id_cultivo, nombre=fila.nombre_cultivo),
        especie=ESPECIES.a_codigo(fila.nombre_especie),
        preliminar=_preliminar(fila),
        tecnico=_tecnico(fila),
        enviado_a_panel_en=fila.fecha_envio_panel,
        vence_en=fila.vence_en,
    )


def permisos(fila: CasoFila, actor: UsuarioActual) -> tuple[bool, bool, bool]:
    """(puede_diagnosticar, puede_cerrar, puede_asignar) para quien consulta."""
    estado = _estado(fila)
    es_lider = actor.rol is Rol.LIDER_TECNICO
    asignado_a_mi = fila.id_tecnico == actor.id
    return (
        not es_lider and asignado_a_mi and estado is EstadoCaso.EN_REVISION,  # el líder no diagnostica (D-02)
        estado is EstadoCaso.REGISTRADO and (es_lider or asignado_a_mi),
        es_lider and es_asignable(estado),
    )


class CasosService:
    def __init__(self, session: AsyncSession, s3: S3StorageClient | None = None) -> None:
        self.session = session
        self.s3 = s3

    async def _fila_o_404(self, actor: UsuarioActual, id_caso: str) -> CasoFila:
        fila = await repo.obtener(self.session, alcance_de(actor), id_caso)
        if fila is None:  # fuera del alcance es igual que inexistente: no se revela que existe
            raise no_encontrado()
        return fila

    # --- Bandeja ---------------------------------------------------------------------------------------------------

    async def listar(
        self, actor: UsuarioActual, filtros: FiltrosCasos, estado: EstadoCaso | None, pagina: ParametrosPagina
    ) -> tuple[list[CasoResumen], int]:
        filas, total = await repo.listar(self.session, alcance_de(actor), filtros, estado, pagina)
        return [a_resumen(f) for f in filas], total

    async def conteos(self, actor: UsuarioActual, filtros: FiltrosCasos) -> ConteosCasos:
        por_estado = await repo.conteos(self.session, alcance_de(actor), filtros)
        conteo = {ESTADOS_CASO.a_codigo(nombre): n for nombre, n in por_estado.items()}
        return ConteosCasos(
            todos=sum(por_estado.values()), **{k: v for k, v in conteo.items() if k in ConteosCasos.model_fields}
        )

    async def navegacion(self, actor: UsuarioActual) -> tuple[int, int]:
        return await repo.navegacion(self.session, alcance_de(actor))

    # --- Detalle ---------------------------------------------------------------------------------------------------

    async def detalle(self, actor: UsuarioActual, id_caso: str) -> CasoDetalle:
        fila = await self._fila_o_404(actor, id_caso)
        resumen = a_resumen(fila)
        resumenes = await repo.resumenes(self.session, id_caso)
        puede_diagnosticar, puede_cerrar, puede_asignar = permisos(fila, actor)

        porcentajes: list[PorcentajeCategoria] = []
        resumen_texto: str | None = None
        coincidencias_texto: list[CoincidenciaTexto] = []
        if fila.id_identificacion_preliminar:
            filas_pct = await repo.coincidencias(self.session, fila.id_identificacion_preliminar)
            porcentajes = [
                PorcentajeCategoria(categoria=CATEGORIAS.a_codigo(n), porcentaje=float(p)) for n, p in filas_pct
            ]
            ident = await repo.identificacion(self.session, fila.id_identificacion_preliminar)
            if ident:
                resumen_texto = ident["explicacion_resultado"]
                if resumen.preliminar and resumen.preliminar.resultado is ResultadoAnalisis.AMBIGUO:
                    detalle = _detalle_json(ident)
                    coincidencias_texto = [
                        CoincidenciaTexto(
                            categoria=p.categoria, texto="; ".join(detalle.get(p.categoria, {}).get("coinciden", []))
                        )
                        for p in porcentajes[:2]
                    ]

        diag = await repo.diagnostico(self.session, id_caso)
        diagnostico = None
        if diag is not None:
            diagnostico = DiagnosticoOut(
                resultado_tecnico=diag.resultado_tecnico,
                categoria_determinada=CATEGORIAS.a_codigo(diag.categoria) if diag.categoria else None,
                motivo_visita=diag.motivo_visita,
                descripcion=diag.descripcion or "",
                observaciones=diag.observaciones or "",
                tecnico_responsable=resumen.tecnico,
                registrado_en=diag.fecha_diagnostico,
            )
        ultimo = resumenes.ultimo_evento
        return CasoDetalle(
            id=resumen.id,
            codigo=resumen.codigo,
            estado=resumen.estado,
            productor=ProductorDetalle(
                **resumen.productor.model_dump(),
                casos_ultimo_anio=await repo.casos_del_productor_ultimo_anio(self.session, fila.id_productor),
            ),
            finca=FincaDetalle(
                **resumen.finca.model_dump(),
                latitud=None if fila.latitud_finca is None else float(fila.latitud_finca),
                longitud=None if fila.longitud_finca is None else float(fila.longitud_finca),
            ),
            cultivo=CultivoDetalle(
                **resumen.cultivo.model_dump(),
                tipo=fila.tipo_cultivo,
                especies=await repo.especies_del_cultivo(self.session, fila.id_cultivo),
            ),
            especie=resumen.especie,
            zona_afectada=fila.descripcion_zona_afectada or "",
            preliminar=resumen.preliminar,
            preliminar_porcentajes=porcentajes,
            preliminar_resumen=resumen_texto,
            preliminar_coincidencias=coincidencias_texto,
            tecnico=resumen.tecnico,
            enviado_a_panel_en=resumen.enviado_a_panel_en,
            vence_en=resumen.vence_en,
            cerrado_en=fila.fecha_cierre,
            evidencias_resumen=EvidenciasResumen(
                total=sum(resumenes.fotos_por_tipo.values()),
                maximo=MAXIMO_FOTOS_UTILES,
                por_tipo=resumenes.fotos_por_tipo,
            ),
            cuestionario_resumen=CuestionarioResumen(
                respuestas=resumenes.respuestas, desde=resumenes.primera_respuesta, hasta=resumenes.ultima_respuesta
            ),
            historial_ultimo=HistorialUltimo(titulo=TITULOS_EVENTO.get(ultimo[0], ultimo[1]), en=ultimo[2])
            if ultimo
            else None,
            diagnostico=diagnostico,
            puede_diagnosticar=puede_diagnosticar,
            puede_cerrar=puede_cerrar,
            puede_asignar=puede_asignar,
        )

    async def evidencias(self, actor: UsuarioActual, id_caso: str) -> list[EvidenciaOut]:
        await self._fila_o_404(actor, id_caso)
        expira = datetime.now(UTC) + timedelta(seconds=VIGENCIA_URL_SEGUNDOS)
        return [
            EvidenciaOut(
                id=e.id,
                tipo=e.tipo,
                numero=e.numero,
                util=e.util,
                descripcion=e.respuesta,
                pregunta=e.pregunta,
                recibida_en=e.recibida_en,
                url=self.s3.url_prefirmada(e.clave) if self.s3 else None,
                url_expira_en=expira if self.s3 else None,
            )
            for e in await repo.evidencias(self.session, id_caso)
        ]

    async def cuestionario(self, actor: UsuarioActual, id_caso: str) -> CuestionarioOut:
        await self._fila_o_404(actor, id_caso)
        filas = await repo.cuestionario(self.session, id_caso)
        return CuestionarioOut(
            desde=filas[0][2] if filas else None,
            hasta=filas[-1][2] if filas else None,
            respuestas=[RespuestaOut(pregunta=p, respuesta=r) for p, r, _ in filas],
        )

    async def historial(self, actor: UsuarioActual, id_caso: str) -> list[EventoOut]:
        """El técnico solo ve el historial de los casos que tiene asignados; el líder, el de todos (guía §2).

        Al final van los pasos que faltan (asignación, diagnóstico, cierre) con `pendiente=true`, como los muestra
        la línea de tiempo del panel. Los estados finales (Cerrado, Descartado) no tienen pasos pendientes.
        """
        fila = await self._fila_o_404(actor, id_caso)
        ocurridos = [
            EventoOut(
                tipo=e.tipo,
                titulo=TITULOS_EVENTO.get(e.tipo, e.tipo.replace("_", " ").capitalize()),
                en=e.fecha,
                detalle=e.descripcion,
                responsable=_responsable(e.actor_tipo, e.actor_id, e.actor_nombre),
            )
            for e in await repo.eventos(self.session, id_caso)
        ]
        return ocurridos + _pasos_pendientes(_estado(fila))

    async def explicacion(self, actor: UsuarioActual, id_caso: str) -> ExplicacionOut:
        fila = await self._fila_o_404(actor, id_caso)
        ident = (
            await repo.identificacion(self.session, fila.id_identificacion_preliminar)
            if fila.id_identificacion_preliminar
            else None
        )
        if ident is None:
            raise ApiError(404, "SIN_ANALISIS", "El chatbot aún no termina el análisis.")
        ajustes = get_settings()
        resultado = ResultadoAnalisis(ident["tipo_resultado"])
        porcentajes = [
            PorcentajeCategoria(categoria=CATEGORIAS.a_codigo(n), porcentaje=float(p))
            for n, p in await repo.coincidencias(self.session, fila.id_identificacion_preliminar or "")
        ]
        detalle = _detalle_json(ident)
        propuesta = (
            None if resultado is ResultadoAnalisis.NO_IDENTIFICADO or not porcentajes else porcentajes[0].categoria
        )
        umbral = ajustes.umbral_coincidencia
        reglas = {
            ResultadoAnalisis.PRELIMINAR: f"Identificado: una categoría alcanzó el {umbral} % de coincidencia o más.",
            ResultadoAnalisis.AMBIGUO: (
                f"Ambiguo: dos categorías alcanzaron el {umbral} % con menos de "
                f"{ajustes.umbral_ambiguo} puntos de diferencia."
            ),
            ResultadoAnalisis.NO_IDENTIFICADO: f"No identificado: ninguna categoría llegó al {umbral} %.",
        }
        return ExplicacionOut(
            coincide=list(detalle.get(propuesta, {}).get("coinciden", [])) if propuesta else [],
            descarta=[
                DescartaOut(categoria=p.categoria, texto="; ".join(detalle[p.categoria]["descartan"]))
                for p in porcentajes
                if p.categoria != propuesta and detalle.get(p.categoria, {}).get("descartan")
            ],
            cercanas=[p for p in porcentajes if p.categoria != propuesta and p.porcentaje < umbral],
            texto=ident["explicacion_resultado"],
            regla=reglas[resultado],
        )

    # --- Asignación ------------------------------------------------------------------------------------------------

    async def asignar(self, actor: UsuarioActual, id_caso: str, id_tecnico: str) -> CasoResumen:
        """Pendiente de revisión → En revisión; reasignar (En revisión) conserva el estado."""
        nombre_estado = await repo.bloquear_caso(self.session, id_caso)
        if nombre_estado is None:
            raise no_encontrado()
        estado = EstadoCaso(ESTADOS_CASO.a_codigo(nombre_estado))
        if not es_asignable(estado):
            await self.session.rollback()
            raise ApiError(409, "TRANSICION_INVALIDA", f"Un caso en estado «{nombre_estado}» ya no admite asignación.")
        nombre_tecnico = await repo.tecnico_activo(self.session, id_tecnico)
        if nombre_tecnico is None:
            await self.session.rollback()
            raise ApiError(422, "VALIDACION", "Revisa los campos marcados.", {"tecnico_id": "Elige un técnico activo."})
        vigente = await repo.tecnico_vigente(self.session, id_caso)
        if vigente is not None and vigente[0] == id_tecnico:
            await self.session.rollback()
            raise ApiError(409, "CASO_YA_ASIGNADO", f"El caso ya está asignado a {nombre_tecnico}.")

        try:
            await self.session.execute(sp.ESTABLECER_ACTOR, {"tipo": "LIDER", "id": actor.id})
            await repo.reasignar(self.session, id_caso, id_tecnico)
            anterior = vigente[1] if vigente else "sin técnico"
            if estado is EstadoCaso.PENDIENTE_REVISION:
                validar_transicion(estado, EstadoCaso.EN_REVISION)
                await repo.pasar_a_estado(self.session, id_caso, EstadoCaso.EN_REVISION)  # el trigger anota el evento
            else:
                await self.session.execute(
                    sp.REGISTRAR_EVENTO,
                    {
                        "id_caso": id_caso,
                        "tipo_evento": "TECNICO_REASIGNADO",
                        "descripcion": f"El caso pasó de {anterior} a {nombre_tecnico}.",
                        "actor_tipo": "LIDER",
                        "actor_id": actor.id,
                        "fecha": None,
                    },
                )
            await auditar(
                self.session,
                actor,
                "ASIGNAR_CASO" if vigente is None else "REASIGNAR_CASO",
                "casos",
                f"Caso {id_caso} asignado a {id_tecnico}",
                anterior={"tecnico": vigente[0]} if vigente else None,
                nuevo={"tecnico": id_tecnico},
            )
            await self.session.commit()
        except DBAPIError as error:
            await self.session.rollback()
            if sp.error_de_negocio(error) is sp.SqlState.TRANSICION_INVALIDA:
                raise ApiError(409, "TRANSICION_INVALIDA", "El caso ya no admite asignación.") from None
            raise
        fila = await self._fila_o_404(actor, id_caso)
        return a_resumen(fila)

    # --- Diagnóstico y cierre ----------------------------------------------------------------------------------------

    async def diagnosticar(self, actor: UsuarioActual, id_caso: str, datos: DiagnosticoIn) -> CasoDetalle:
        """Completa el diagnóstico (En revisión → Registrado). Solo el técnico de la asignación vigente.

        `CONFIRMADO` confirma la categoría que propuso la identificación preliminar (P-04); si esa identificación
        fue ambigua o no identificó nada, el técnico usa `OTRA_PATOLOGIA` y elige la categoría. Tras registrar una
        categoría transmisible se evalúan las alertas, en la misma transacción.
        """
        fila = await self._fila_o_404(actor, id_caso)  # el técnico ajeno o con la asignación cerrada recibe 404
        estado = _estado(fila)
        if estado is not EstadoCaso.EN_REVISION:
            ya_tiene = estado in (EstadoCaso.REGISTRADO, EstadoCaso.CERRADO)
            raise ApiError(
                409,
                "TRANSICION_INVALIDA",
                "Este caso ya tiene diagnóstico."
                if ya_tiene
                else "El caso debe estar En revisión para diagnosticarlo.",
            )
        categoria, motivo = self._validar_diagnostico(datos, fila)
        try:
            id_categoria = await repo.id_categoria(self.session, categoria) if categoria else None
            await repo.registrar_diagnostico(
                self.session,
                id_caso,
                actor.id,
                datos.resultado_tecnico.value,
                id_categoria,
                motivo,
                datos.descripcion,
                datos.observaciones,
            )
            if id_categoria and await repo.categoria_transmisible(self.session, id_categoria):
                await repo.evaluar_alerta(self.session, id_categoria)
            await self.session.commit()
        except DBAPIError as error:
            await self.session.rollback()
            raise _traducir(error) from None
        return await self.detalle(actor, id_caso)

    @staticmethod
    def _validar_diagnostico(datos: DiagnosticoIn, fila: CasoFila) -> tuple[str | None, str | None]:
        """(categoría que se guarda, motivo que se guarda); `422` con los campos que fallan."""
        campos: dict[str, str] = {}
        resultado = datos.resultado_tecnico
        categoria: str | None = datos.categoria_determinada
        motivo = datos.motivo_visita
        if resultado is ResultadoTecnico.CONFIRMADO:
            propuesta = (
                CATEGORIAS.a_codigo(fila.nombre_categoria_principal)
                if fila.resultado_analisis == ResultadoAnalisis.PRELIMINAR.value and fila.nombre_categoria_principal
                else None
            )
            if propuesta is None:
                campos["resultado_tecnico"] = (
                    "Solo se confirma una identificación preliminar. Elige «otra patología» e indica la categoría."
                )
            elif categoria is not None and categoria != propuesta:
                campos["categoria_determinada"] = (
                    "Para confirmar usa la categoría propuesta; si es otra, elige «otra patología»."
                )
            categoria = propuesta
        elif resultado is ResultadoTecnico.OTRA_PATOLOGIA:
            if categoria is None:
                campos["categoria_determinada"] = "Selecciona la categoría fitosanitaria que determinaste."
        else:
            categoria = None  # sin una categoría determinada no hay nada que guardar
        if resultado is ResultadoTecnico.REQUIERE_VISITA:
            if motivo is None:
                campos["motivo_visita"] = "Escribe el motivo de la visita."
        else:
            motivo = None
        if campos:
            raise ApiError(422, "VALIDACION", "Revisa los campos marcados.", campos)
        return categoria, motivo

    async def cerrar(self, actor: UsuarioActual, id_caso: str) -> CasoDetalle:
        """Registrado → Cerrado, con el vencimiento de conservación. Líder: cualquiera; técnico: los suyos."""
        fila = await self._fila_o_404(actor, id_caso)
        estado = _estado(fila)
        if estado is not EstadoCaso.REGISTRADO:
            raise ApiError(
                409,
                "TRANSICION_INVALIDA",
                "El caso ya está cerrado."
                if estado is EstadoCaso.CERRADO
                else "Guarda el diagnóstico técnico antes de cerrar el caso.",
            )
        try:
            await repo.cerrar_caso(
                self.session,
                id_caso,
                "LIDER" if actor.rol is Rol.LIDER_TECNICO else "TECNICO",
                actor.id,
                get_settings().meses_conservacion,
            )
            await self.session.commit()
        except DBAPIError as error:
            await self.session.rollback()
            raise _traducir(error) from None
        return await self.detalle(actor, id_caso)


def _traducir(error: DBAPIError) -> ApiError | DBAPIError:
    """Los errores de negocio de los procedimientos SQL (AN001…AN004) pasan a la respuesta de la API."""
    negocio = sp.error_de_negocio(error)
    if negocio is sp.SqlState.TRANSICION_INVALIDA:
        return ApiError(409, "TRANSICION_INVALIDA", "El caso ya no está en el estado que exige esta acción.")
    if negocio in (sp.SqlState.NO_ES_EL_TECNICO_ASIGNADO, sp.SqlState.NO_ENCONTRADO):
        return no_encontrado()  # perdió la asignación mientras tanto: para él el caso ya no existe
    if negocio is sp.SqlState.DATOS_INVALIDOS:
        return ApiError(422, "VALIDACION", "Revisa los campos marcados.")
    return error


def _detalle_json(ident: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """`detalle_explicacion` por categoría: `{CÓDIGO: {porcentaje, coinciden: [...], descartan: [...]}}` (D-14)."""
    bruto = ident.get("detalle_explicacion")
    return {k: v for k, v in bruto.items() if isinstance(v, dict)} if isinstance(bruto, dict) else {}


def _responsable(tipo: str | None, id_actor: str | None, nombre: str | None) -> ResponsableEvento | None:
    if tipo is None:
        return None
    if tipo == "SISTEMA":
        return ResponsableEvento(tipo=tipo, id=None, nombre="Sistema")
    return ResponsableEvento(tipo=tipo, id=id_actor, nombre=nombre or "Cuenta eliminada")
