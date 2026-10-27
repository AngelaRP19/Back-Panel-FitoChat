"""Agrega los routers del panel bajo /api/v1. Cada módulo de `modules/panel_api` suma el suyo aquí."""

from fastapi import APIRouter

from app.modules.panel_api.cuentas.router import router as cuentas_router
from app.modules.panel_api.cuentas.router_auth import router as auth_router
from app.modules.panel_api.cuentas.router_tecnicos import router as tecnicos_router

router = APIRouter(prefix="/api/v1")
router.include_router(auth_router)
router.include_router(cuentas_router)
router.include_router(tecnicos_router)
