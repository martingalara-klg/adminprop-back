"""tests/unit/shared/test_tenant_rbac.py

SDD: docs/skills/api-endpoint.md §"Extraccion de organization_id del JWT" /
§"Verificacion de permisos". core/sdd_03_api_contracts.md §"Convenciones
Generales" (RN-D01) + §"Resumen de Autorizacion por Recurso".

Tests unitarios directos de `shared/tenant.py` y `shared/rbac.py` (issue
#9, primitivos nuevos) -- sin pasar por HTTP, invocando las dependencies
directamente con un `JWTPayload` construido a mano.
"""

from __future__ import annotations

import uuid

import pytest

from adminprop.shared.auth.jwt import JWTPayload
from adminprop.shared.errors.codes import ForbiddenException, UnauthorizedException
from adminprop.shared.rbac import requires_permission
from adminprop.shared.tenant import get_current_tenant

pytestmark = pytest.mark.asyncio


class TestGetCurrentTenant:
    async def test_returns_org_id_for_regular_jwt(self):
        org_id = uuid.uuid4()
        payload = JWTPayload(
            sub=uuid.uuid4(),
            org_id=org_id,
            role="owner",
            permissions=["user:manage"],
            is_super_admin=False,
        )

        result = await get_current_tenant(payload)

        assert result == org_id

    async def test_super_admin_jwt_raises_unauthorized(self):
        """RN-D01: un JWT de Super Admin (sin `org`) no puede resolver un
        tenant -- se rechaza en vez de continuar con `org_id=None`."""
        payload = JWTPayload(
            sub=uuid.uuid4(),
            org_id=None,
            role=None,
            permissions=[],
            is_super_admin=True,
        )

        with pytest.raises(UnauthorizedException):
            await get_current_tenant(payload)

    async def test_jwt_without_org_id_raises_unauthorized(self):
        """Defensivo: `is_super_admin=False` pero `org_id=None` (JWT
        corrupto/malformado) tambien se rechaza."""
        payload = JWTPayload(
            sub=uuid.uuid4(), org_id=None, role=None, permissions=[], is_super_admin=False
        )

        with pytest.raises(UnauthorizedException):
            await get_current_tenant(payload)


class TestRequiresPermission:
    async def test_allows_payload_with_permission(self):
        payload = JWTPayload(
            sub=uuid.uuid4(),
            org_id=uuid.uuid4(),
            role="owner",
            permissions=["user:manage", "role:read"],
            is_super_admin=False,
        )
        check = requires_permission("user:manage")

        result = await check(payload)

        assert result is payload

    async def test_rejects_payload_without_permission(self):
        """sdd_03 §"Resumen de Autorizacion por Recurso": el chequeo es
        por permiso atomico, nunca por `role_name`."""
        payload = JWTPayload(
            sub=uuid.uuid4(),
            org_id=uuid.uuid4(),
            role="admin",
            permissions=["contract:read"],
            is_super_admin=False,
        )
        check = requires_permission("user:manage")

        with pytest.raises(ForbiddenException):
            await check(payload)
