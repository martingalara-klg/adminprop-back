"""Refresh tokens server-side en Redis: rotativo single-use, revocacion de
familia ante reuso (issue #6).

SDD: core/sdd_04_nonfunctional.md parrafo 2.2 ("refresh token 30 dias
rotativo single-use (reuso de un refresh ya rotado -> revoca la familia
completa). Refresh tokens server-side en Redis (revocables).").

Diseno:
- El valor de cookie (raw token) es un secreto opaco de alta entropia
  (secrets.token_urlsafe). Redis nunca guarda el valor en claro -- se
  indexa por su hash SHA-256, mismo patron que organization_invitations.token
  (issue #5: token es UNIQUE, hash del token, nunca el token en claro).
- Cada token pertenece a una "familia" (family_id, un UUID por sesion de
  login). Cada registro guarda un flag `used`: al rotar, el token
  presentado se marca usado (no se borra) y se emite uno nuevo en la
  misma familia. Si el mismo token vuelve a presentarse ya marcado
  `used=true`, es reuso (robo tras la rotacion legitima) -> se revoca
  toda la familia y el intento falla con UnauthorizedException. Guardar
  el registro usado (en vez de borrarlo) es lo que permite trazar el
  reuso hasta su familia -- un registro borrado no tiene a donde volver.
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
# Issue #8 (reset-password): indice inverso user_id -> {family_id, ...}
# para poder revocar "todas las sesiones" de un usuario en una sola
# operacion -- invariante de seguridad de sdd_04 §2.2 "refresh tokens
# revocables" que el issue #8 explota al cambiar el password.
_USER_FAMILIES_PREFIX = "auth:refresh:user_families:"


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _token_key(token_hash: str) -> str:
    return f"{_TOKEN_PREFIX}{token_hash}"


def _family_key(family_id: str) -> str:
    return f"{_FAMILY_PREFIX}{family_id}"


def _user_families_key(user_id: UUID) -> str:
    return f"{_USER_FAMILIES_PREFIX}{user_id}"


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
        issued = await self._issue_token(
            user_id=user_id, organization_id=organization_id, family_id=family_id
        )
        # Issue #8: registrar la familia en el indice del usuario para
        # poder revocar todas sus sesiones de una vez (reset-password).
        # Mismo TTL que el token para que el set no sobreviva indefinidamente
        # a familias ya vencidas y purgadas de Redis.
        await self._redis.sadd(_user_families_key(user_id), family_id)
        await self._redis.expire(_user_families_key(user_id), self._ttl_seconds)
        return issued

    async def _issue_token(
        self, *, user_id: UUID, organization_id: UUID | None, family_id: str
    ) -> RefreshTokenIssued:
        raw_token = secrets.token_urlsafe(32)
        token_hash = _hash_token(raw_token)
        record = {
            "user_id": str(user_id),
            "organization_id": str(organization_id) if organization_id is not None else "",
            "family_id": family_id,
            "used": False,
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

        Token desconocido/expirado -> UnauthorizedException (no hay familia
        a la que asociarlo). Token conocido pero ya marcado `used` -> reuso
        detectado -> revoca la familia completa y levanta
        UnauthorizedException. Caso normal: marca `used=true` y emite el
        siguiente token de la misma familia.
        """
        token_hash = _hash_token(raw_token)
        raw_record = await self._redis.get(_token_key(token_hash))
        if raw_record is None:
            raise UnauthorizedException()

        data = json.loads(raw_record)
        family_id = data["family_id"]

        if data.get("used"):
            # Reuso: alguien presenta un token ya rotado -- robo probable.
            await self.revoke_family(family_id)
            raise UnauthorizedException()

        ttl = await self._redis.ttl(_token_key(token_hash))
        data["used"] = True
        await self._redis.set(
            _token_key(token_hash),
            json.dumps(data),
            ex=ttl if ttl and ttl > 0 else self._ttl_seconds,
        )

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
        """Revoca todos los tokens (usados o no) de la familia (reuso o logout)."""
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

    async def revoke_all_families_for_user(self, user_id: UUID) -> None:
        """Issue #8 (reset-password): cierra todas las sesiones existentes
        del usuario -- revoca cada familia de refresh token conocida via el
        indice `_user_families_key`. Buena practica de seguridad tras un
        cambio de password (no es un CA formal del issue, pero es
        exactamente el escenario para el que sdd_04 §2.2 declara "refresh
        tokens server-side en Redis (revocables)")."""
        key = _user_families_key(user_id)
        family_ids = await self._redis.smembers(key)
        for family_id in family_ids:
            await self.revoke_family(family_id)
        await self._redis.delete(key)
