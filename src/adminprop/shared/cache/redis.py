"""Cliente Redis async compartido (issue #6).

SDD: core/sdd_04_nonfunctional.md §2.2 ("Refresh tokens server-side en
Redis, revocables"), §2.5 (rate limiting Redis token bucket). Reusa
`settings.redis_url` -- la misma instancia que broker/backend de Celery
(decision ya tomada en el issue #4, `workers/celery_app.py`); el volumen
de referencia del MVP no justifica una instancia separada.

Cacheado por proceso con `lru_cache`, igual patron que
`db/session.get_engine` -- los tests que necesiten un cliente fresco por
test deben `get_redis_client.cache_clear()` (ver
tests/integration/auth/conftest.py), evitando el problema conocido de
"Future attached to a different loop" entre tests async.
"""

from __future__ import annotations

from functools import lru_cache

from redis.asyncio import Redis

from adminprop.config import get_settings


@lru_cache
def get_redis_client() -> Redis:
    """Cliente Redis async cacheado por proceso (una sola pool de conexiones)."""
    settings = get_settings()
    return Redis.from_url(settings.redis_url, decode_responses=True)
