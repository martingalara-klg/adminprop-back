"""Chequeo de permisos atomicos del JWT (issue #9).

SDD: core/sdd_03_api_contracts.md §"Resumen de Autorizacion por Recurso"
("El chequeo es por permiso atomico ... nunca por nombre de rol.") +
docs/skills/api-endpoint.md §"Verificacion de permisos".
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine

from fastapi import Depends

from adminprop.shared.audit.service import record_access_denied
from adminprop.shared.auth.dependencies import get_current_access_token_payload
from adminprop.shared.auth.jwt import JWTPayload
from adminprop.shared.errors.codes import ForbiddenException


def requires_permission(
    permission: str,
) -> Callable[..., Coroutine[None, None, JWTPayload]]:
    """Factory de dependency: exige `permission` en `payload.permissions[]`.

    sdd_03 §"Resumen de Autorizacion por Recurso": el chequeo es siempre
    por permiso atomico del array `permissions[]` del JWT, nunca por
    `role_name` (el `role` del JWT es informativo, no se usa para decidir
    autorizacion).
    """

    async def _check(
        payload: JWTPayload = Depends(get_current_access_token_payload),
    ) -> JWTPayload:
        if permission not in payload.permissions:
            # RN-A04: "todo intento de acceso no autorizado queda
            # registrado en el log de auditoria". `payload.org_id` es
            # None solo si un JWT de Super Admin llega aca (caso fuera de
            # alcance de `audit_logs`, ver `record_access_denied`).
            await record_access_denied(
                organization_id=payload.org_id,
                user_id=payload.sub,
                details={"permission": permission},
            )
            raise ForbiddenException()
        return payload

    return _check
