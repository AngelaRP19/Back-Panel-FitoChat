"""Agrega los routers del panel bajo /api/v1. Cada módulo de `modules/panel_api` suma el suyo aquí."""

from fastapi import APIRouter

from app.modules.panel_api.alertas.router import router as alertas_router
from app.modules.panel_api.casos.router import router as casos_router
from app.modules.panel_api.consulta.router import router as consulta_router
from app.modules.panel_api.cuentas.router import router as cuentas_router
from app.modules.panel_api.cuentas.router_auth import router as auth_router
from app.modules.panel_api.cuentas.router_tecnicos import router as tecnicos_router
from app.modules.panel_api.fincas.router import catalogos as catalogos_router
from app.modules.panel_api.fincas.router import router as fincas_router
from app.modules.panel_api.productores.router import router as productores_router
from app.modules.panel_api.reportes.router import router as reportes_router
from app.modules.panel_api.resumen.router import router as resumen_router

router = APIRouter(prefix="/api/v1")
router.include_router(auth_router)
router.include_router(cuentas_router)
router.include_router(tecnicos_router)
router.include_router(productores_router)
router.include_router(fincas_router)
router.include_router(catalogos_router)
router.include_router(consulta_router)
router.include_router(casos_router)
router.include_router(resumen_router)
router.include_router(alertas_router)
router.include_router(reportes_router)
