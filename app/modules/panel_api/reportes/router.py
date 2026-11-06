"""Reportes del panel (guía §11, módulo 12): por ahora solo los de **alertas** (ACTA-003). Solo el líder técnico.

`GET /reportes/alertas` devuelve el reporte como KPI + secciones, la forma que ya dibuja el panel. La exportación a
PDF y CSV (`/reportes/alertas/exportar`) usa los mismos filtros y los mismos datos.
"""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ErrorRespuesta
from app.core.roles import Rol
from app.core.security_cognito import UsuarioActual, requiere_rol
from app.infrastructure.db.session import get_session
from app.modules.panel_api.reportes.exportar import a_csv, a_pdf
from app.modules.panel_api.reportes.filtros import Comunicada, FiltrosAlertas, Periodo, estados_de
from app.modules.panel_api.reportes.schemas import ReporteDatos
from app.modules.panel_api.reportes.service import ReportesService

router = APIRouter(prefix="/reportes", tags=["reportes"])

SoloLider = Annotated[UsuarioActual, Depends(requiere_rol(Rol.LIDER_TECNICO))]
RESPUESTAS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorRespuesta, "description": "Sin token, vencido o inválido."},
    403: {"model": ErrorRespuesta, "description": "Solo el líder técnico."},
    422: {"model": ErrorRespuesta, "description": "VALIDACION: fechas invertidas o estados que no existen."},
}


def get_servicio(session: Annotated[AsyncSession, Depends(get_session)]) -> ReportesService:
    return ReportesService(session)


Servicio = Annotated[ReportesService, Depends(get_servicio)]


def filtros_alertas(
    periodo: Annotated[
        Periodo | None, Query(description="Sin periodo: «personalizado» si hay fechas; si no, `anio`")
    ] = None,
    desde: Annotated[date | None, Query(description="Solo con periodo personalizado")] = None,
    hasta: Annotated[date | None, Query(description="Solo con periodo personalizado")] = None,
    vereda: Annotated[str | None, Query(description="Código de la vereda, p. ej. SAN_LUIS")] = None,
    estado_alerta: Annotated[str | None, Query(description="Estados separados por coma")] = None,
    decidida: Annotated[str | None, Query(description="`sin` o el id del líder que decidió")] = None,
    casos_min: Annotated[int | None, Query(ge=0, description="Mínimo de casos de la alerta")] = None,
    comunicada: Annotated[Comunicada | None, Query(description="`si` o `sin_comunicar` (confirmadas)")] = None,
) -> FiltrosAlertas:
    return FiltrosAlertas(
        periodo, desde, hasta, vereda or None, estados_de(estado_alerta), decidida or None, casos_min, comunicada
    )


Filtros = Annotated[FiltrosAlertas, Depends(filtros_alertas)]


@router.get("/alertas", response_model=ReporteDatos, response_model_exclude_none=True, responses=RESPUESTAS)
async def reporte_de_alertas(_: SoloLider, filtros: Filtros, servicio: Servicio) -> ReporteDatos:
    """Alertas del periodo por estado, vereda y mes, tiempos de decisión, avisos y parámetros vigentes."""
    return await servicio.alertas(filtros)


@router.get("/alertas/exportar", responses={**RESPUESTAS, 200: {"description": "Archivo CSV o PDF."}})
async def exportar_alertas(
    _: SoloLider,
    filtros: Filtros,
    servicio: Servicio,
    formato: Annotated[Literal["csv", "pdf"], Query(description="Formato del archivo")] = "csv",
) -> Response:
    documento = await servicio.documento(filtros)
    contenido, tipo = (
        (a_csv(documento), "text/csv; charset=utf-8") if formato == "csv" else (a_pdf(documento), "application/pdf")
    )
    return Response(
        contenido,
        media_type=tipo,
        headers={"Content-Disposition": f'attachment; filename="{documento.nombre_archivo}.{formato}"'},
    )
