"""Agrega los routers del panel bajo /api/v1. Cada módulo de `modules/panel_api` suma el suyo aquí."""

from fastapi import APIRouter

from app.modules.panel_api.cuentas.router import router as cuentas_router

router = APIRouter(prefix="/api/v1")
router.include_router(cuentas_router)
