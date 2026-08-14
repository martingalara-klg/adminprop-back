"""Tokens de reset de password: un solo uso, TTL logico 1h (issue #8).

SDD: core/sdd_03_api_contracts.md §1 ("GET /auth/reset-password/:token ->
200 | 404 | 410", "POST /auth/reset-password -> 200"). core/sdd_04_nonfunctional.md
§2.2 ("Passwords: ... Refresh tokens server-side en Redis (revocables).").

Decision de implementacion (declarada en el PR del issue #8): igual que
`refresh_store.py` (issue #6), un secreto de un solo uso con TTL corto no
necesita persistencia relacional ni pasar por Alembic -- Redis alcanza y
es coherente con el resto de los tokens efimeros del modulo auth.

El valor de `password_reset_token_grace_seconds` (mayor al TTL logico de
`password_reset_token_ttl_seconds`) es lo que permite a
`GET /auth/reset-password/:token` distinguir "el token nunca existio / ya
fue consumido" (404, generico) de "existio pero vencio" (410,
RESET_TOKEN_EXPIRED): si la key de Redis se borrara exactamente al vencer
la ventana logica, ambos casos lucirian identicos (ausencia de la key).
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends
from redis.asyncio import Redis

from adminprop.config import Settings, get_settings
from adminprop.shared.cache.redis import get_redis_client

_TOKEN_PREFIX = "auth:password_reset:token:"


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _token_key(token_hash: str) -> str:
    return f"{_TOKEN_PREFIX}{token_hash}"


@dataclass(frozen=True)
class PasswordResetTokenStatus:
    """Resultado de consultar/consumir un token.

    `exists=False` cubre tanto "nunca se emitio" como "ya fue consumido"
    (la key se borra en `consume`) -- ambos casos son indistinguibles a
    proposito (no hay motivo para diferenciarlos de cara al cliente).
    """

    exists: bool
    expired: bool
    user_id: UUID | None
    email: str | None


class PasswordResetTokenStore:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._redis = redis
        self._settings = settings

    async def issue(self, *, user_id: UUID, email: str) -> str:
        """Emite un token nuevo. No invalida tokens previos del mismo
        usuario explicitamente: son de un solo uso y TTL corto, así que
        varios pedidos de "olvide mi password" en la misma hora pueden
        coexistir sin riesgo -- el primero que se use gana."""
        raw_token = secrets.token_urlsafe(32)
        record = {
            "user_id": str(user_id),
            "email": email,
            "expires_at": time.time() + self._settings.password_reset_token_ttl_seconds,
        }
        await self._redis.set(
            _token_key(_hash_token(raw_token)),
            json.dumps(record),
            ex=self._settings.password_reset_token_grace_seconds,
        )
        return raw_token

    async def peek(self, raw_token: str) -> PasswordResetTokenStatus:
        """Lectura sin consumir -- GET /auth/reset-password/:token."""
        return await self._read(raw_token, delete=False)

    async def consume(self, raw_token: str) -> PasswordResetTokenStatus:
        """Valida y borra el token (single-use) -- POST /auth/reset-password.

        Se borra la key tanto si el token es valido como si esta vencido
        (un token vencido presentado explicitamente no debe poder
        reintentarse una vez que el cliente ya vio el error).
        """
        return await self._read(raw_token, delete=True)

    async def _read(self, raw_token: str, *, delete: bool) -> PasswordResetTokenStatus:
        key = _token_key(_hash_token(raw_token))
        raw_record = await self._redis.get(key)
        if raw_record is None:
            return PasswordResetTokenStatus(exists=False, expired=False, user_id=None, email=None)

        data = json.loads(raw_record)
        if delete:
            await self._redis.delete(key)

        expired = time.time() > data["expires_at"]
        return PasswordResetTokenStatus(
            exists=True,
            expired=expired,
            user_id=UUID(data["user_id"]),
            email=data["email"],
        )


def get_password_reset_token_store(
    redis: Redis = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> PasswordResetTokenStore:
    return PasswordResetTokenStore(redis, settings)
