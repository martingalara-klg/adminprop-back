"""Dependency FastAPI: extraer el access token de la cookie (issue #6).

Usada por `/v1/auth/logout` (necesita saber que sesion cerrar) y por
futuros modulos protegidos (`get_current_tenant`, issue #7/#9) que
decodifican el JWT antes de resolver `organization_id`/permisos.

sdd_03 parrafo "Convenciones Generales": "JWT RS256 en HttpOnly Secure
cookies (server-set en login) ... El header Authorization: Bearer solo
para testing/server-to-server."
"""

from __future__ import annotations

import logging

from fastapi import Depends, Request

from adminprop.shared.audit.service import record_access_denied
from adminprop.shared.auth.cookies import ACCESS_TOKEN_COOKIE
from adminprop.shared.auth.jwt import JWTPayload, decode_access_token
from adminprop.shared.errors.codes import SuperAdminRequiredException, UnauthorizedException

logger = logging.getLogger(__name__)


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


async def requires_super_admin(
    request: Request,
    payload: JWTPayload = Depends(get_current_access_token_payload),
) -> JWTPayload:
    """Dependency de `/superadmin/*` (issue #7).

    SDD: core/spec_module_00_superadmin.md RN-01 ("el JWT del Super Admin
    no contiene `org` ni `role`; opera con el rol PostgreSQL
    adminprop_superadmin solo en /superadmin/*") + CA-00-05 ("un usuario
    owner/admin/maintenance que intenta acceder a /superadmin/* recibe
    403 SUPERADMIN_REQUIRED y el intento queda auditado").

    RN-A04 (issue #10): el intento denegado se persiste en `audit_logs`
    via `record_access_denied` -- se mantiene tambien el `logger.warning`
    estructurado (request_id ya viaja via ContextVar, sdd_04 §4.1) para
    observabilidad en tiempo real, independiente del audit trail
    persistido.
    """
    if not payload.is_super_admin:
        logger.warning(
            "superadmin access denied",
            extra={
                "user_id": str(payload.sub),
                "path": request.url.path,
                "service": "superadmin",
            },
        )
        # RN-A04: `payload.org_id` es None solo si un token ya de Super
        # Admin llegara aca -- imposible por construccion (is_super_admin
        # es False en esta rama) -- pero `record_access_denied` igual
        # tolera None sin persistir (ver su docstring).
        await record_access_denied(
            organization_id=payload.org_id,
            user_id=payload.sub,
            details={"path": request.url.path},
        )
        raise SuperAdminRequiredException()
    return payload
