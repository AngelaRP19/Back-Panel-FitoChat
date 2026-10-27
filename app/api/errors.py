import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ErrorRespuesta(BaseModel):
    """Cuerpo de todo error de la API (sirve para documentarlo en el OpenAPI)."""

    codigo: str
    mensaje: str
    campos: dict[str, str] | None = None


class ApiError(Exception):
    """Error de negocio con el formato único de la API: {codigo, mensaje, campos?}."""

    def __init__(
        self,
        status_code: int,
        codigo: str,
        mensaje: str,
        campos: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(mensaje)
        self.status_code = status_code
        self.codigo = codigo
        self.mensaje = mensaje
        self.campos = campos
        self.headers = headers


def error_response(
    status_code: int,
    codigo: str,
    mensaje: str,
    campos: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, object] = {"codigo": codigo, "mensaje": mensaje}
    if campos:
        body["campos"] = campos
    return JSONResponse(status_code=status_code, content=body, headers=headers)


_CODIGOS_HTTP: dict[int, tuple[str, str]] = {
    400: ("SOLICITUD_INVALIDA", "La solicitud no es válida."),
    401: ("NO_AUTENTICADO", "No autenticado."),
    403: ("SIN_PERMISO", "No tienes permiso para esta acción."),
    404: ("NO_ENCONTRADO", "Recurso no encontrado."),
    405: ("SOLICITUD_INVALIDA", "Método no permitido."),
    413: ("SOLICITUD_INVALIDA", "El cuerpo de la solicitud es demasiado grande."),
    429: ("DEMASIADAS_SOLICITUDES", "Demasiadas solicitudes. Intenta de nuevo en un momento."),
}


async def _api_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return error_response(exc.status_code, exc.codigo, exc.mensaje, exc.campos, exc.headers)


async def _http_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    codigo, mensaje = _CODIGOS_HTTP.get(exc.status_code, ("ERROR_INTERNO", "Ocurrió un error."))
    return error_response(exc.status_code, codigo, mensaje)


async def _validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    campos = {".".join(str(p) for p in err["loc"][1:]) or "cuerpo": err["msg"] for err in exc.errors()}
    return error_response(status.HTTP_422_UNPROCESSABLE_ENTITY, "VALIDACION", "Revisa los datos enviados.", campos)


async def _unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Error no controlado", exc_info=exc)
    return error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "ERROR_INTERNO", "Ocurrió un error. Intenta de nuevo.")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
