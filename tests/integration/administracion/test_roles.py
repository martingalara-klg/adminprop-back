"""tests/integration/administracion/test_roles.py

SDD: docs/sdd/features/spec_module_07_administracion.md RF-03.
core/sdd_03_api_contracts.md §3 "GET /roles (role:read; solo lectura en MVP)".
"""

from __future__ import annotations

import pytest

from adminprop.modules.superadmin.provisioning import (
    ADMIN_PERMISSIONS,
    MAINTENANCE_PERMISSIONS,
    OWNER_PERMISSIONS,
)

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
