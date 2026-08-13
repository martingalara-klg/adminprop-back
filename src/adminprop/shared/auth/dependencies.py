"""Dependency FastAPI: extraer el access token de la cookie (issue #6).

Usada por `/v1/auth/logout` (necesita saber que sesion cerrar) y por
futuros modulos protegidos (`get_current_tenant`, issue #7/#9) que
decodifican el JWT antes de resolver `organization_id`/permisos.

sdd_03 parrafo "Convenciones Generales": "JWT RS256 en HttpOnly Secure
cookies (server-set en login) ... El header Authorization: Bearer solo
para testing/server-to-server."
"""

from __future__ import annotations

from fastapi import Request

from adminprop.shared.auth.cookies import ACCESS_TOKEN_COOKIE
from adminprop.shared.auth.jwt import JWTPayload, decode_access_token
from adminprop.shared.errors.codes import UnauthorizedException


def _extract_raw_access_token(request: Request) -> str:
    token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    if not token:
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[len("bearer ") :]
    if not token:
        raise UnauthorizedException()
    return token


async def get_current_access_token_payload(request: Request) -> JWTPayload:
    """Decodifica el access token de la cookie (o header Bearer en tests/server-to-server)."""
    return decode_access_token(_extract_raw_access_token(request))
