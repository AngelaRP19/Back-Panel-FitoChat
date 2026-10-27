"""Reportes de alertas: lee las alertas del periodo, las filtra y arma el reporte o su documento exportable."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.catalogos_api import EstadoAlerta, normalizar_codigo
from app.modules.panel_api.alertas import repository as repo
from app.modules.panel_api.reportes import alertas as reporte_alertas
from app.modules.panel_api.reportes.exportar import documento_de
from app.modules.panel_api.reportes.filtros import FiltrosAlertas, rango_del_periodo
from app.modules.panel_api.reportes.schemas import Documento, ReporteDatos
from app.modules.panel_api.territorio_comun import veredas_registradas

BOGOTA = ZoneInfo("America/Bogota")


class ReportesService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

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
