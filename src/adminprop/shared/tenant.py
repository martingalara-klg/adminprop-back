"""Extraccion de `organization_id` desde el JWT (issue #9).

SDD: docs/skills/api-endpoint.md §"Extraccion de organization_id del JWT" +
core/sdd_03_api_contracts.md §"Convenciones Generales" ("organization_id
nunca viaja en body, path ni query -- siempre se deriva del JWT").

Primer modulo (administracion, issue #9) que necesita esta dependency de
forma generica -- se conecta al `JWTPayload` real de `shared/auth/jwt.py`
(el skill usa un `decode_jwt` de pseudocodigo que no existe en este repo).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import Depends

from adminprop.shared.auth.dependencies import get_current_access_token_payload
from adminprop.shared.auth.jwt import JWTPayload
from adminprop.shared.errors.codes import UnauthorizedException


async def get_current_tenant(
    payload: JWTPayload = Depends(get_current_access_token_payload),
) -> UUID:
    """RN-D01: `organization_id` SIEMPRE del JWT, nunca de body/path/query.

    Un JWT de Super Admin (sin `org`) o corrupto no puede resolver un
    tenant -- se rechaza con 401 en vez de continuar con `org_id=None`.
    """
    if payload.is_super_admin or payload.org_id is None:
        raise UnauthorizedException()
    return payload.org_id
