"""Endpoints públicos de acceso (guía §10.1): estado del panel, primer líder y verificación de correo."""

from fastapi import APIRouter, Depends, Request, status

from app.api.errors import ApiError
from app.core.rate_limit import LimitadorVentana
from app.modules.panel_api.cuentas.dependencias import RESPUESTAS_ESCRITURA, ServicioCuentas
from app.modules.panel_api.cuentas.schemas import (
    EstadoAuth,
    PrimerLiderIn,
    PrimerLiderOut,
    VerificarCorreoIn,
    VerificarCorreoOut,
)

router = APIRouter(prefix="/auth", tags=["autenticación"])

# Propuesta de la guía (P-B03): 5 solicitudes por minuto y por IP, para frenar la enumeración de correos.
LIMITADOR_VERIFICAR_CORREO = LimitadorVentana(maximo=5, ventana_segundos=60)


def limitar_verificacion_de_correo(request: Request) -> None:
    clave = request.client.host if request.client else "desconocido"
    if not LIMITADOR_VERIFICAR_CORREO.permitir(clave):
        espera = LIMITADOR_VERIFICAR_CORREO.segundos_para_reintentar(clave)
        raise ApiError(
            429,
            "DEMASIADAS_SOLICITUDES",
            "Hiciste demasiadas consultas. Espera un momento e intenta de nuevo.",
            headers={"Retry-After": str(espera)},
        )


@router.get("/estado", response_model=EstadoAuth)
async def estado_del_panel(servicio: ServicioCuentas) -> EstadoAuth:
    """Si aún no hay ningún líder técnico, el login lleva a la pantalla de primer uso."""
    return EstadoAuth(hay_lider=await servicio.hay_lider())


@router.post(
    "/primer-lider", status_code=status.HTTP_201_CREATED, response_model=PrimerLiderOut, responses=RESPUESTAS_ESCRITURA
)
async def crear_primer_lider(datos: PrimerLiderIn, servicio: ServicioCuentas) -> PrimerLiderOut:
    """Crea al primer líder técnico (RQF01). Solo mientras no exista ninguno: después, `409 YA_EXISTE_LIDER`."""
    return PrimerLiderOut(id=await servicio.primer_lider(datos))


@router.post(
    "/recuperacion/verificar-correo",
    response_model=VerificarCorreoOut,
    dependencies=[Depends(limitar_verificacion_de_correo)],
    responses={429: RESPUESTAS_ESCRITURA[422]},
)
async def verificar_correo(datos: VerificarCorreoIn, servicio: ServicioCuentas) -> VerificarCorreoOut:
    """RQF06: dice si el correo tiene cuenta, para mostrar «No hay ninguna cuenta registrada con este correo»."""
    return VerificarCorreoOut(existe=await servicio.correo_registrado(datos.correo))
