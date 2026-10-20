from fastapi import FastAPI

from app.api import salud_router, v1_webhook_router
from app.api.errors import register_error_handlers
from app.core.config import get_settings
from app.core.logging import CorrelationIdMiddleware, configure_logging


def create_app() -> FastAPI:
    settings = get_settings()  # falla rápido si falta una variable obligatoria
    configure_logging(settings.log_level)

    application = FastAPI(title="ANUC Zetaquirá — Backend", version="0.1.0")
    application.add_middleware(CorrelationIdMiddleware)
    register_error_handlers(application)

    application.include_router(salud_router.router)
    application.include_router(v1_webhook_router.router)
    return application


app = create_app()
