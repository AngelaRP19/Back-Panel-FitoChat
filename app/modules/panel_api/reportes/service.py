"""Reportes del panel: alertas (R3, con su documento exportable), los generales R1, R2, R4 y R5, el resumen y las
consultas guardadas (D-19, sin envío programado por D-21)."""

import logging
from datetime import UTC, date, datetime
from typing import get_args
from zoneinfo import ZoneInfo

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.core.catalogos_api import EstadoAlerta, normalizar_codigo
from app.core.security_cognito import UsuarioActual
from app.infrastructure.consultas_store import Consulta, ConsultasStore, nuevo_id
from app.modules.panel_api.alertas import repository as repo
from app.modules.panel_api.reportes import alertas as reporte_alertas
from app.modules.panel_api.reportes import datos as datos_reportes
from app.modules.panel_api.reportes import generales
from app.modules.panel_api.reportes.exportar import documento_de
from app.modules.panel_api.reportes.filtros import (
    Comunicada,
    FiltrosAlertas,
    Periodo,
    estados_de,
    rango_del_periodo,
)
from app.modules.panel_api.reportes.schemas import (
    ConsultaGuardada,
    DatosConsulta,
    Documento,
    ReporteDatos,
    ResumenReportes,
    TipoReporte,
    Visibilidad,
)
from app.modules.panel_api.territorio_comun import veredas_registradas

logger = logging.getLogger(__name__)

BOGOTA = ZoneInfo("America/Bogota")
SINGULAR = {"casos": "caso", "alertas": "alerta", "productores": "productor", "cuentas": "cuenta"}


def _entero(texto: str | None, campo: str) -> int | None:
    if not texto:
        return None
    if not texto.isdigit():
        raise ApiError(422, "VALIDACION", "Revisa los campos marcados.", {campo: "Escribe un número entero."})
    return int(texto)


def filtros_alertas_de(f: generales.FiltrosReporte) -> FiltrosAlertas:
    """Filtros como texto (los de una consulta guardada o del resumen) → filtros del reporte de alertas."""
    periodo = f.get("periodo") or None
    if periodo is not None and periodo not in get_args(Periodo):
        raise ApiError(422, "VALIDACION", "Revisa los campos marcados.", {"periodo": "Periodo desconocido."})
    comunicada = f.get("comunicada") or None
    return FiltrosAlertas(
        periodo,  # type: ignore[arg-type]
        generales.fecha(f.get("desde"), "desde"),
        generales.fecha(f.get("hasta"), "hasta"),
        f.get("vereda") or None,
        estados_de(f.get("estado_alerta")),
        f.get("decidida") or None,
        _entero(f.get("casos_min"), "casos_min"),
        comunicada if comunicada in get_args(Comunicada) else None,  # type: ignore[arg-type]
    )


def resultado_de(reporte: ReporteDatos) -> str:
    """«6 casos», «1 productor»: el total del reporte con su sustantivo."""
    return generales.plural(reporte.total, SINGULAR.get(reporte.sustantivo, reporte.sustantivo), reporte.sustantivo)


class ReportesService:
    def __init__(self, session: AsyncSession, consultas: ConsultasStore | None = None) -> None:
        self.session = session
        self.consultas = consultas

    async def _ahora(self) -> datetime:
        ahora = await self.session.scalar(text("SELECT fn_ahora()"))
        assert isinstance(ahora, datetime)
        return ahora.astimezone(BOGOTA)

    async def _calcular(self, f: FiltrosAlertas) -> tuple[ReporteDatos, date, date, dict[str, str]]:
        ahora = await self._ahora()
        desde, hasta, _ = rango_del_periodo(f, ahora.date())
        veredas = await veredas_registradas(self.session)
        alertas = reporte_alertas.filtrar(await repo.en_periodo(self.session, desde, hasta), f)
        reporte = reporte_alertas.construir(
            alertas,
            await repo.parametros(self.session),
            await repo.ultimas_ediciones(self.session),
            veredas,
            desde,
            hasta,
        )
        return reporte, desde, hasta, veredas

    async def alertas(self, f: FiltrosAlertas) -> ReporteDatos:
        return (await self._calcular(f))[0]

    async def documento(self, f: FiltrosAlertas) -> Documento:
        reporte, desde, hasta, veredas = await self._calcular(f)
        estados = ", ".join(EstadoAlerta(e).value.replace("_", " ").capitalize() for e in f.estados)
        nombre_vereda = veredas.get(normalizar_codigo(f.vereda), f.vereda) if f.vereda else "todas"
        chips = [f"Vereda: {nombre_vereda}", f"Estados: {estados or 'todos'}"]
        if f.decidida:
            chips.append("Decidió: sin decisión" if f.decidida == "sin" else f"Decidió: {f.decidida}")
        if f.casos_min is not None:
            chips.append(f"Casos: al menos {f.casos_min}")
        if f.comunicada:
            chips.append("Comunicadas: sí" if f.comunicada == "si" else "Comunicadas: sin comunicar")
        ahora = await self._ahora()
        return documento_de(reporte, desde, hasta, chips, ahora.strftime("%d/%m/%Y a las %H:%M"))

    # --- R1, R2, R4, R5 y resumen -------------------------------------------------------------------------------------

    async def general(
        self, tipo: str, f: generales.FiltrosReporte, datos: datos_reportes.Datos | None = None
    ) -> ReporteDatos:
        """R1 (`casos`), R2 (`identificacion`), R4 (`productores`) o R5 (`tecnicos`)."""
        ahora = await self._ahora()
        generales.rango(f, generales.dia(ahora))  # valida el periodo antes de leer los datos
        return generales.GENERADORES[tipo](datos or await datos_reportes.cargar(self.session), f, ahora)

    async def reporte(
        self, tipo: str, f: generales.FiltrosReporte, datos: datos_reportes.Datos | None = None
    ) -> ReporteDatos:
        """Cualquiera de los cinco reportes con los filtros como texto (los de una consulta guardada)."""
        if tipo == "alertas":
            return await self.alertas(filtros_alertas_de(f))
        return await self.general(tipo, f, datos)

    async def resumen(self, f: generales.FiltrosReporte) -> ResumenReportes:
        """Pantalla 23: KPI, tarjetas de los cinco reportes y secciones. El R3 usa el periodo y la vereda."""
        ahora = await self._ahora()
        generales.rango(f, generales.dia(ahora))
        alertas = await self.alertas(
            filtros_alertas_de({k: v for k, v in f.items() if k in ("periodo", "desde", "hasta", "vereda")})
        )
        return generales.resumen(await datos_reportes.cargar(self.session), f, ahora, alertas)

    # --- Consultas guardadas (D-19) -----------------------------------------------------------------------------------

    def _store(self) -> ConsultasStore:
        assert self.consultas is not None, "ReportesService sin store de consultas"
        return self.consultas

    async def _a_salida(self, c: Consulta, datos: datos_reportes.Datos) -> ConsultaGuardada:
        try:
            resultado = resultado_de(await self.reporte(c.tipo, c.filtros, datos))
        except ApiError:
            resultado = "Filtros no válidos"  # p. ej., fechas guardadas que ya no forman un periodo válido
        tipo: TipoReporte = c.tipo  # type: ignore[assignment]
        visibilidad: Visibilidad = c.visibilidad  # type: ignore[assignment]
        return ConsultaGuardada(
            id=c.id,
            nombre=c.nombre,
            tipo=tipo,
            descripcion=c.descripcion,
            filtros=c.filtros,
            resultado=resultado,
            visibilidad=visibilidad,
            creada_por=c.creada_por_nombre,
        )

    async def listar_consultas(self, actor: UsuarioActual) -> list[ConsultaGuardada]:
        """Las del equipo y las propias (las «yo» de otros líderes no se ven), de la más reciente a la más antigua."""
        visibles = [
            c
            for c in await run_in_threadpool(self._store().listar)
            if c.visibilidad == "equipo" or c.creada_por == actor.id
        ]
        if not visibles:
            return []
        visibles.sort(key=lambda c: c.creada_en or datetime.min.replace(tzinfo=UTC), reverse=True)
        datos = await datos_reportes.cargar(self.session)
        return [await self._a_salida(c, datos) for c in visibles]

    async def guardar_consulta(self, actor: UsuarioActual, d: DatosConsulta) -> ConsultaGuardada:
        """`envio` se ignora (D-21). Sin descripción, la del título del reporte."""
        consulta = Consulta(
            id=nuevo_id(),
            nombre=d.nombre,
            tipo=d.tipo,
            descripcion=d.descripcion or generales.TITULOS[d.tipo],
            filtros={k: v for k, v in d.filtros.items() if v},
            visibilidad=d.visibilidad,
            creada_por=actor.id,
            creada_por_nombre=actor.nombre,
            creada_en=datetime.now(UTC),
        )
        salida = await self._a_salida(consulta, await datos_reportes.cargar(self.session))
        try:
            await run_in_threadpool(self._store().guardar, consulta)
        except Exception:
            logger.exception("No se pudo guardar la consulta de reportes")
            raise ApiError(503, "ERROR_INTERNO", "Las consultas guardadas no están disponibles por ahora.") from None
        return salida

    async def borrar_consulta(self, actor: UsuarioActual, id_consulta: str) -> None:
        """Solo quien la guardó. Una «yo» de otro líder no existe para los demás (404)."""
        consulta = await run_in_threadpool(self._store().obtener, id_consulta)
        if consulta is None or (consulta.visibilidad == "yo" and consulta.creada_por != actor.id):
            raise ApiError(404, "NO_ENCONTRADO", "La consulta no existe.")
        if consulta.creada_por != actor.id:
            raise ApiError(403, "SIN_PERMISO", "Solo quien guardó la consulta puede eliminarla.")
        await run_in_threadpool(self._store().borrar, id_consulta)
