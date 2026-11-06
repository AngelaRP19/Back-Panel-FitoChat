"""Reportes del panel (guía §11, módulo 12; pantallas 23 y 24). Solo el líder técnico.

Cada reporte es KPI + secciones, la forma que ya dibuja el panel: `GET /reportes/alertas` (R3, con exportación a PDF y
CSV en `/reportes/alertas/exportar`) y `GET /reportes/{tipo}` para R1 `casos`, R2 `identificacion`, R4 `productores` y
R5 `tecnicos` (filtros F1, F3 y F4 como parámetros de consulta; listas separadas por coma; la exportación de estos la
hace el navegador). `GET /reportes/resumen` arma la pantalla 23 y `/reportes/consultas` guarda consultas (D-19).

Las rutas fijas van antes de `/{tipo}` para que no choquen.
"""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError, ErrorRespuesta
from app.core.roles import Rol
from app.core.security_cognito import UsuarioActual, requiere_rol
from app.infrastructure.consultas_store import ConsultasStore, get_consultas_store
from app.infrastructure.db.session import get_session
from app.modules.panel_api.reportes.exportar import a_csv, a_pdf
from app.modules.panel_api.reportes.filtros import Comunicada, FiltrosAlertas, Periodo, estados_de
from app.modules.panel_api.reportes.generales import GENERADORES, FiltrosReporte
from app.modules.panel_api.reportes.schemas import ConsultaGuardada, DatosConsulta, ReporteDatos, ResumenReportes
from app.modules.panel_api.reportes.service import ReportesService

router = APIRouter(prefix="/reportes", tags=["reportes"])

SoloLider = Annotated[UsuarioActual, Depends(requiere_rol(Rol.LIDER_TECNICO))]
RESPUESTAS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorRespuesta, "description": "Sin token, vencido o inválido."},
    403: {"model": ErrorRespuesta, "description": "Solo el líder técnico."},
    422: {"model": ErrorRespuesta, "description": "VALIDACION: fechas invertidas o estados que no existen."},
}


def get_servicio(
    session: Annotated[AsyncSession, Depends(get_session)],
    consultas: Annotated[ConsultasStore, Depends(get_consultas_store)],
) -> ReportesService:
    return ReportesService(session, consultas)


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


def filtros_reporte(request: Request) -> FiltrosReporte:
    """Todos los parámetros de consulta, como viven en la URL del panel (listas separadas por coma)."""
    return {k: v for k, v in request.query_params.items() if v}


FiltrosTexto = Annotated[FiltrosReporte, Depends(filtros_reporte)]


@router.get("/resumen", response_model=ResumenReportes, response_model_exclude_none=True, responses=RESPUESTAS)
async def resumen_de_reportes(_: SoloLider, filtros: FiltrosTexto, servicio: Servicio) -> ResumenReportes:
    """Pantalla 23: KPI del periodo, tarjetas de los cinco reportes y secciones (filtros F1)."""
    return await servicio.resumen(filtros)


@router.get("/consultas", response_model=list[ConsultaGuardada], responses=RESPUESTAS)
async def listar_consultas(actor: SoloLider, servicio: Servicio) -> list[ConsultaGuardada]:
    """Consultas del equipo y las propias, con el resultado actual («6 casos»). Sin envío programado (D-21)."""
    return await servicio.listar_consultas(actor)


@router.post(
    "/consultas",
    status_code=status.HTTP_201_CREATED,
    response_model=ConsultaGuardada,
    responses={**RESPUESTAS, 503: {"model": ErrorRespuesta, "description": "Consultas no disponibles."}},
)
async def guardar_consulta(actor: SoloLider, datos: DatosConsulta, servicio: Servicio) -> ConsultaGuardada:
    """Nombre de 5 a 60 caracteres, reporte, filtros y visibilidad (`yo` o `equipo`). `envio` se ignora."""
    return await servicio.guardar_consulta(actor, datos)


@router.delete(
    "/consultas/{id_consulta}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**RESPUESTAS, 404: {"model": ErrorRespuesta, "description": "La consulta no existe."}},
)
async def borrar_consulta(actor: SoloLider, id_consulta: str, servicio: Servicio) -> Response:
    """Solo quien la guardó (403 para los demás)."""
    await servicio.borrar_consulta(actor, id_consulta)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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


@router.get(
    "/{tipo}",
    response_model=ReporteDatos,
    response_model_exclude_none=True,
    responses={**RESPUESTAS, 404: {"model": ErrorRespuesta, "description": "El reporte no existe."}},
)
async def reporte_general(_: SoloLider, tipo: str, filtros: FiltrosTexto, servicio: Servicio) -> ReporteDatos:
    """R1 `casos`, R2 `identificacion`, R4 `productores` o R5 `tecnicos`. Rango máximo de 12 meses."""
    if tipo not in GENERADORES:
        raise ApiError(404, "NO_ENCONTRADO", "El reporte no existe.")
    return await servicio.general(tipo, filtros)
