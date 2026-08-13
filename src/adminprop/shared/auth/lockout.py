"""Lockout de login por email (issue #6).

SDD: core/sdd_04_nonfunctional.md §2.1/§2.2 ("Fuerza bruta de login:
Lockout (5 intentos/10 min -> 30 min) + rate limit"). core/sdd_03_api_contracts.md
"Lockout: 5 intentos fallidos en 10 min -> ACCOUNT_LOCKED por 30 min."

Clave por email (no por IP): el objetivo es proteger una cuenta puntual
de fuerza bruta distribuida, no limitar una IP (eso ya lo cubre el rate
limit de sdd_04 §2.5 sobre el endpoint). Guardado en Redis -- state
efimero, TTL nativo evita tener que barrer registros vencidos.
"""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from adminprop.config import Settings

_FAILURE_COUNTER_PREFIX = "auth:login:failures:"
_LOCK_PREFIX = "auth:login:locked:"


def _failure_key(email: str) -> str:
    return f"{_FAILURE_COUNTER_PREFIX}{email.lower()}"


def _lock_key(email: str) -> str:
    return f"{_LOCK_PREFIX}{email.lower()}"


@dataclass(frozen=True)
class LockStatus:
    locked: bool
    retry_after_seconds: int = 0


class LoginLockout:
    """RN-D01/sdd_04 §2.2: lockout por email, 5 intentos/10min -> 30min."""

    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._redis = redis
        self._settings = settings

    async def check(self, email: str) -> LockStatus:
        """Retorna si `email` esta actualmente bloqueado y el countdown restante."""
        ttl = await self._redis.ttl(_lock_key(email))
        if ttl and ttl > 0:
            return LockStatus(locked=True, retry_after_seconds=ttl)
        return LockStatus(locked=False)

    async def register_failure(self, email: str) -> LockStatus:
        """Incrementa el contador de fallos; aplica el lock si llega al umbral.

        Retorna el estado de lock resultante (para que el caller decida si
        el intento actual ya debe verse como ACCOUNT_LOCKED o como
        UNAUTHORIZED generico -- decision de implementacion: el intento que
        cruza el umbral todavia responde UNAUTHORIZED, el lock aplica desde
        el proximo intento, evitando revelar informacion extra sobre el
        conteo interno).
        """
        key = _failure_key(email)
        count = await self._redis.incr(key)
        if count == 1:
            await self._redis.expire(key, self._settings.login_lockout_window_seconds)

        if count >= self._settings.login_lockout_max_attempts:
            await self._redis.set(
                _lock_key(email),
                "1",
                ex=self._settings.login_lockout_duration_seconds,
            )
            await self._redis.delete(key)
            return LockStatus(
                locked=True, retry_after_seconds=self._settings.login_lockout_duration_seconds
            )
        return LockStatus(locked=False)

    async def reset(self, email: str) -> None:
        """Login exitoso: limpia contador y lock (si hubiera)."""
        await self._redis.delete(_failure_key(email), _lock_key(email))
