"""JWT RS256: emision/decodificacion del access token (issue #6).

SDD: core/sdd_03_api_contracts.md parrafo "Convenciones Generales"
("Shape del JWT: sub, org, role, permissions[], is_super_admin. En
/superadmin/* el JWT no lleva org ni role.") + core/sdd_04_nonfunctional.md
§2.2 ("JWT RS256 (clave asimetrica). Access token 8h.").

Las claves privada/publica viven en filesystem local (RUNBOOK-LOCAL-001
§2.3, `openssl genrsa`) -- nunca commiteadas (.gitignore `keys/`). Se
cachean por proceso (`lru_cache`) igual patron que `db/session.get_engine`;
los tests que generan un par de claves efimero deben `cache_clear()` los
loaders (ver tests/integration/auth/conftest.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from uuid import UUID

import jwt as pyjwt

from adminprop.config import get_settings
from adminprop.shared.errors.codes import UnauthorizedException


@lru_cache
def _load_private_key() -> str:
    settings = get_settings()
    return Path(settings.jwt_private_key_path).read_text(encoding="utf-8")


@lru_cache
def _load_public_key() -> str:
    settings = get_settings()
    return Path(settings.jwt_public_key_path).read_text(encoding="utf-8")


def clear_key_cache() -> None:
    """Invalida las claves cacheadas -- usado por tests que regeneran claves."""
    _load_private_key.cache_clear()
    _load_public_key.cache_clear()


@dataclass(frozen=True)
class JWTPayload:
    """Claims decodificados del access token.

    sdd_03 "Shape del JWT": `sub` (user_id), `org` (organization_id o None
    para Super Admin), `role` (nombre o None), `permissions[]`,
    `is_super_admin`.
    """

    sub: UUID
    org_id: UUID | None
    role: str | None
    permissions: list[str] = field(default_factory=list)
    is_super_admin: bool = False
    exp: int = 0
    jti: str = ""


def create_access_token(
    *,
    user_id: UUID,
    organization_id: UUID | None,
    role: str | None,
    permissions: list[str],
    is_super_admin: bool,
    jti: str,
) -> str:
    """Emite un access token RS256 (sdd_04 §2.2: TTL 8h por default)."""
    import time

    settings = get_settings()
    now = int(time.time())
    payload: dict[str, object] = {
        "sub": str(user_id),
        "org": str(organization_id) if organization_id is not None else None,
        "role": role,
        "permissions": permissions,
        "is_super_admin": is_super_admin,
        "iat": now,
        "exp": now + settings.jwt_access_token_ttl_seconds,
        "jti": jti,
    }
    return pyjwt.encode(payload, _load_private_key(), algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> JWTPayload:
    """Decodifica y valida un access token. Levanta `UnauthorizedException` si es
    ausente/expirado/invalido (sdd_03 §"Codigos de Error Globales" -- 401 UNAUTHORIZED).
    """
    settings = get_settings()
    try:
        claims = pyjwt.decode(token, _load_public_key(), algorithms=[settings.jwt_algorithm])
    except pyjwt.PyJWTError as exc:
        raise UnauthorizedException() from exc

    return JWTPayload(
        sub=UUID(claims["sub"]),
        org_id=UUID(claims["org"]) if claims.get("org") else None,
        role=claims.get("role"),
        permissions=list(claims.get("permissions") or []),
        is_super_admin=bool(claims.get("is_super_admin", False)),
        exp=int(claims.get("exp", 0)),
        jti=str(claims.get("jti", "")),
    )
