"""Rate limiting con contador de ventana fija en Redis (issue #6).

SDD: core/sdd_04_nonfunctional.md §2.5 ("Rate limiting (Redis token
bucket)"). docs/skills/api-endpoint.md "Rate limiting".

Decision de implementacion: se usa un contador de ventana fija
(`INCR` + `EXPIRE` una sola vez) en vez de un token-bucket real con
refill continuo. Para los limites de sdd_04 §2.5 (conteos bajos por
ventanas de minutos/horas) el comportamiento observable es equivalente
(N requests por ventana), y el contador de ventana fija es mas simple de
razonar/testear que un bucket con refill fraccionario -- se documenta
aca por si un modulo futuro necesita smoothing real (ej: escrituras
generales 120/min) y prefiere migrar a un algoritmo con refill continuo.
"""

from __future__ import annotations

from fastapi import Request

from adminprop.shared.cache.redis import get_redis_client
from adminprop.shared.errors.codes import RateLimitExceededException

_PREFIX = "rate_limit:"


async def check_rate_limit(*, key: str, max_requests: int, window_seconds: int) -> None:
    """Incrementa el contador `key` y levanta `RateLimitExceededException` si excede
    `max_requests` dentro de `window_seconds`.
    """
    redis = get_redis_client()
    redis_key = f"{_PREFIX}{key}"
    count = await redis.incr(redis_key)
    if count == 1:
        await redis.expire(redis_key, window_seconds)

    if count > max_requests:
        ttl = await redis.ttl(redis_key)
        retry_after = ttl if ttl and ttl > 0 else window_seconds
        raise RateLimitExceededException(details={"retry_after_seconds": retry_after})


def rate_limit_by_ip(bucket: str, max_requests: int, window_seconds: int):
    """Factory de dependency FastAPI: limita por IP de origen.

    Ej: `Depends(rate_limit_by_ip("auth_login", 10, 600))` ==
    sdd_04 §2.5 "POST /auth/login: 10 req / IP / 10 min".
    """

    async def _check(request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        await check_rate_limit(
            key=f"{bucket}:{client_ip}", max_requests=max_requests, window_seconds=window_seconds
        )

    return _check
