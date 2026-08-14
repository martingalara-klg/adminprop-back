"""tests/integration/administracion/test_roles.py

SDD: docs/sdd/features/spec_module_07_administracion.md RF-03.
core/sdd_03_api_contracts.md §3 "GET /roles (role:read; solo lectura en MVP)".
Implements: CA-07-03 ("Intentar editar un rol de sistema devuelve 422
SYSTEM_ROLE_IMMUTABLE").
"""

from __future__ import annotations

import uuid

import pytest

from adminprop.modules.administracion.repository import RoleRow
from adminprop.modules.administracion.service import RoleService
from adminprop.modules.superadmin.provisioning import (
    ADMIN_PERMISSIONS,
    MAINTENANCE_PERMISSIONS,
    OWNER_PERMISSIONS,
)
from adminprop.shared.errors.codes import SystemRoleImmutableException

pytestmark = pytest.mark.asyncio


class TestGetRoles:
    async def test_get_roles_returns_the_3_system_roles_with_permissions(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )

        response = await client.get("/v1/roles", headers=owner["headers"])

        assert response.status_code == 200
        roles_by_name = {item["name"]: item for item in response.json()["data"]}
        assert set(roles_by_name) == {"owner", "admin", "maintenance"}
        assert sorted(roles_by_name["owner"]["permissions"]) == sorted(OWNER_PERMISSIONS)
        assert sorted(roles_by_name["admin"]["permissions"]) == sorted(ADMIN_PERMISSIONS)
        assert sorted(roles_by_name["maintenance"]["permissions"]) == sorted(
            MAINTENANCE_PERMISSIONS
        )
        assert all(item["is_system_role"] for item in roles_by_name.values())


class TestCA0703SystemRoleImmutable:
    """CA-07-03: `sdd_03` §3 no define un endpoint de escritura de roles
    en MVP (`GET /roles` es solo lectura); este test cubre la invariante
    RN-03 a nivel de servicio, invocable por cualquier endpoint de
    escritura futuro."""

    def test_ca_07_03_system_role_immutable(self):
        role = RoleRow(
            id=uuid.uuid4(),
            name="owner",
            permissions=list(OWNER_PERMISSIONS),
            is_system_role=True,
        )

        with pytest.raises(SystemRoleImmutableException):
            RoleService.ensure_role_editable(role)

    def test_ensure_role_editable_allows_non_system_roles(self):
        """Defensivo: si en el futuro existieran roles custom
        (`is_system_role=False`, post-MVP segun RF-03), el metodo no
        levanta excepcion."""
        role = RoleRow(
            id=uuid.uuid4(),
            name="custom",
            permissions=["contract:read"],
            is_system_role=False,
        )

        RoleService.ensure_role_editable(role)
