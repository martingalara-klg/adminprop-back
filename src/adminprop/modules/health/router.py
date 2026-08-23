import asyncio

from fastapi import APIRouter, Response

from adminprop.config import get_settings
from adminprop.modules.health import service

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(response: Response) -> dict:
    """Estado del servicio y sus dependencias (sdd_04 §4.7)."""
    settings = get_settings()
    database, redis = await asyncio.gather(
        service.check_tcp(settings.database_url, settings.health_check_timeout_seconds),
        service.check_tcp(settings.redis_url, settings.health_check_timeout_seconds),
    )
    checks = {"database": database, "redis": redis}
    status = "ok" if all(value == "ok" for value in checks.values()) else "degraded"
    response.status_code = 200 if status == "ok" else 503
    return {"data": {"status": status, "checks": checks}}
