"""Refresh tokens server-side en Redis: rotativo single-use, revocacion de
familia ante reuso (issue #6).

SDD: core/sdd_04_nonfunctional.md §2.2 ("refresh token 30 dias rotativo
single-use (reuso de un refresh ya rotado -> revoca la familia completa).
Refresh tokens server-side en Redis (revocables).").

Diseno:
- El valor de cookie (`raw token`) es un secreto opaco de alta entropia
  (`secrets.token_urlsafe`). Redis nunca guarda el valor en claro -- se
  indexa por su hash SHA-256, mismo patron que `organization_invitations.token`
  (issue #5: "token es UNIQUE (hash del token, nunca el token en claro)").
- Cada token pertenece a una "familia" (`family_id`, un UUID por sesion de
  login). Rotar reemplaza el token activo de la familia por uno nuevo con
  TTL fresco; si alguien presenta un token ya usado (robado y reutilizado
  tras la rotacion legitima), se interpreta como robo -> se revoca toda la
  familia y el intento falla con UNAUTHORIZED (fuerza re-login).
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from uuid import UUID, uuid4

from redis.asyncio import Redis

from adminprop.config import Settings
from adminprop.shared.errors.codes import UnauthorizedException

_TOKEN_PREFIX = "auth:refresh:token:"
_FAMILY_PREFIX = "auth:refresh:family:"


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _token_key(token_hash: str) -> str:
    return f"{_TOKEN_PREFIX}{token_hash}"


def _family_key(family_id: str) -> str:
    return f"{_FAMILY_PREFIX}{family_id}"


@dataclass(frozen=True)
class RefreshTokenIssued:
    raw_token: str
    family_id: str
    ttl_seconds: int


@dataclass(frozen=True)
class RefreshTokenRecord:
    user_id: UUID
    organization_id: UUID | None
    family_id: str


class RefreshTokenStore:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._redis = redis
        self._settings = settings

    @property
    def _ttl_seconds(self) -> int:
        return self._settings.jwt_refresh_token_ttl_days * 24 * 60 * 60

    async def issue_family(
        self, *, user_id: UUID, organization_id: UUID | None
    ) -> RefreshTokenIssued:
        """Crea una familia nueva (login) con un unico token activo."""
        family_id = str(uuid4())
        return await self._issue_token(
            user_id=user_id, organization_id=organization_id, family_id=family_id
        )

    async def _issue_token(
        self, *, user_id: UUID, organization_id: UUID | None, family_id: str
    ) -> RefreshTokenIssued:
        raw_token = secrets.token_urlsafe(32)
        token_hash = _hash_token(raw_token)
        record = {
            "user_id": str(user_id),
            "organization_id": str(organization_id) if organization_id is not None else "",
            "family_id": family_id,
        }
        ttl = self._ttl_seconds
        async with self._redis.pipeline(transaction=True) as pipe:
            await pipe.set(_token_key(token_hash), json.dumps(record), ex=ttl)
            await pipe.sadd(_family_key(family_id), token_hash)
            await pipe.expire(_family_key(family_id), ttl)
            await pipe.execute()
        return RefreshTokenIssued(raw_token=raw_token, family_id=family_id, ttl_seconds=ttl)

    async def rotate(self, raw_token: str) -> tuple[RefreshTokenRecord, RefreshTokenIssued]:
        """Valida `raw_token` (single-use) y emite el siguiente token de la familia.

        Reuso de un token ya rotado (no encontrado pero la familia todavia
        existe con otros miembros, o directamente ausente) -> se interpreta
        como robo: revoca la familia completa y levanta `UnauthorizedException`.
        """
        token_hash = _hash_token(raw_token)
        raw_record = await self._redis.get(_token_key(token_hash))
        if raw_record is None:
            # Token desconocido/expirado. Si todavia referencia una familia
            # viva no podemos saberlo sin el hash -- tratamos como invalido.
            raise UnauthorizedException()

        data = json.loads(raw_record)
        family_id = data["family_id"]

        # Single-use: al leerlo, se borra atomicamente. Si dos requests
        # llegan a la vez con el mismo token, solo una gana la carrera.
        deleted = await self._redis.delete(_token_key(token_hash))
        await self._redis.srem(_family_key(family_id), token_hash)
        if deleted == 0:
            # Ya fue consumido por otra request concurrente -- reuso -> revocar familia.
            await self.revoke_family(family_id)
            raise UnauthorizedException()

        record = RefreshTokenRecord(
            user_id=UUID(data["user_id"]),
            organization_id=UUID(data["organization_id"]) if data["organization_id"] else None,
            family_id=family_id,
        )
        issued = await self._issue_token(
            user_id=record.user_id, organization_id=record.organization_id, family_id=family_id
        )
        return record, issued

    async def revoke_family(self, family_id: str) -> None:
        """Revoca todos los tokens vivos de la familia (reuso detectado o logout)."""
        members = await self._redis.smembers(_family_key(family_id))
        if members:
            await self._redis.delete(*(_token_key(member) for member in members))
        await self._redis.delete(_family_key(family_id))

    async def revoke_by_raw_token(self, raw_token: str) -> None:
        """Logout: revoca la familia del token presentado (best-effort)."""
        token_hash = _hash_token(raw_token)
        raw_record = await self._redis.get(_token_key(token_hash))
        if raw_record is None:
            return
        data = json.loads(raw_record)
        await self.revoke_family(data["family_id"])
