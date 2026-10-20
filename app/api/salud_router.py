from fastapi import APIRouter

router = APIRouter(tags=["salud"])


@router.get("/salud")
def salud() -> dict[str, str]:
    """Health check del ALB: sin dependencias externas a propósito."""
    return {"status": "ok"}
