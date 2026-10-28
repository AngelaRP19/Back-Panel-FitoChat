from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import salud_router, v1_panel_router, v1_webhook_router
from app.api.errors import register_error_handlers
from app.core.config import Settings, get_settings
from app.core.logging import REQUEST_ID_HEADER, CorrelationIdMiddleware, configure_logging
from app.core.middleware import BodySizeLimitMiddleware, SecurityHeadersMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()  # falla rápido si falta una variable obligatoria
    configure_logging(settings.log_level)

    # /docs y /openapi.json solo fuera de prod.
    application = FastAPI(
        title="ANUC Zetaquirá — Backend",
        version="0.2.0",
        docs_url=None if settings.en_prod else "/docs",
        redoc_url=None,
        openapi_url=None if settings.en_prod else "/openapi.json",
    )

    # El último que se agrega es el más externo: CORS responde primero (incluidos los errores) y el límite de
    # cuerpo queda dentro del correlation_id para que su 413 también lleve X-Request-Id.
    application.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=settings.max_body_bytes,
        limites_por_ruta={"/api/v1/documentos": settings.max_body_bytes_documentos},
    )
    application.add_middleware(CorrelationIdMiddleware)
    application.add_middleware(SecurityHeadersMiddleware)
    if settings.cors_origenes:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origenes,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
            expose_headers=[REQUEST_ID_HEADER],
            allow_credentials=False,  # la sesión va en el header Authorization, no en cookies
            max_age=600,
        )
    register_error_handlers(application)

    application.include_router(salud_router.router)
    application.include_router(v1_webhook_router.router)
    application.include_router(v1_panel_router.router)
    return application


app = create_app()
