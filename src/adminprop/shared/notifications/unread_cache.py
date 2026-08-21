"""Cache del contador de notificaciones no leidas (issue #31).

SDD: infrastructure/spec_notificaciones.md RF-02 ("badge con contador de
     no leidas") + core/sdd_04_nonfunctional.md §1.4 (tabla de cache):
     "Badge de notificaciones no leidas | 5 min | Al crear/leer
     notificacion".
Implements: CA-NT-04.

Mismo patron que `modules/settlements/job_status.py`: valor operacional
en Redis, TTL fijo, sin persistencia -- si Redis se reinicia el badge
simplemente se recalcula desde Postgres en el siguiente `GET
/notifications` (degradacion segura, no hay perdida de datos de
negocio). Cacheado por `(organization_id, user_id)`: el contador es
propio de cada usuario, nunca compartido entre destinatarios de la
misma organizacion.
"""

from __future__ import annotations

from uuid import UUID

from adminprop.shared.cache.redis import get_redis_client

_KEY_PREFIX = "notifications_unread_count:"
_TTL_SECONDS = 5 * 60


def _key(organization_id: UUID, user_id: UUID) -> str:
    return f"{_KEY_PREFIX}{organization_id}:{user_id}"


async def get_cached_unread_count(organization_id: UUID, user_id: UUID) -> int | None:
    """`None` si no hay valor cacheado (nunca se seteo o expiro el TTL de 5 min)."""
    redis = get_redis_client()
    raw = await redis.get(_key(organization_id, user_id))
    if raw is None:
        return None
    return int(raw)


async def set_cached_unread_count(organization_id: UUID, user_id: UUID, count: int) -> None:
    redis = get_redis_client()
    await redis.set(_key(organization_id, user_id), str(count), ex=_TTL_SECONDS)


async def invalidate_unread_count(organization_id: UUID, user_id: UUID) -> None:
    """sdd_04 §1.4 "Invalidacion: al crear/leer notificacion" -- llamado
    desde `shared/notifications/service.emit()` (al crear) y desde
    `modules/notifications/service.py` (al marcar leida/todas leidas)."""
    redis = get_redis_client()
    await redis.delete(_key(organization_id, user_id))
