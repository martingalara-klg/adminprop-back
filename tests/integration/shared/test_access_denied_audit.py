"""tests/integration/shared/test_access_denied_audit.py

SDD: core/sdd_02_domain_model.md §3 RN-A04 ("Todo intento de acceso no
autorizado queda registrado en el log de auditoria") + §2.17.
Implements: CA-10-03 (access.denied auditado), RN-A04.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


class TestRequiresPermissionDeniedIsAudited:
    """RN-A04 via `shared/rbac.py.requires_permission`."""

    async def test_ca_10_03_forbidden_permission_writes_access_denied_row(
        self, client, seed, audit_logs_reader
    ):
        member = await seed.create_org_with_member(permissions=["contract:read"], role_name="admin")

        response = await client.get("/v1/roles", headers=member["headers"])

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

        rows = await audit_logs_reader(member["organization_id"])
        assert len(rows) == 1
        row = rows[0]
        assert row["action"] == "access.denied"
        assert row["entity_type"] == "access"
        assert row["user_id"] == member["user_id"]
        assert row["after_state"] == {"permission": "role:read"}

    async def test_ca_10_03_allowed_permission_does_not_write_access_denied_row(
        self, client, seed, audit_logs_reader
    ):
        """Contraparte: si el permiso esta presente, no hay evento
        `access.denied` (el request pasa de largo la dependency)."""
        member = await seed.create_org_with_member(permissions=["role:read"], role_name="owner")

        response = await client.get("/v1/roles", headers=member["headers"])

        assert response.status_code == 200
        rows = await audit_logs_reader(member["organization_id"])
        assert rows == []


class TestRequiresSuperAdminDeniedIsAudited:
    """RN-A04 via `shared/auth/dependencies.py.requires_super_admin`."""

    async def test_ca_10_03_superadmin_required_writes_access_denied_row(
        self, client, seed, audit_logs_reader
    ):
        member = await seed.create_org_with_member(
            permissions=["contract:manage", "user:manage"], role_name="owner"
        )

        response = await client.get("/v1/superadmin/organizations", headers=member["headers"])

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "SUPERADMIN_REQUIRED"

        rows = await audit_logs_reader(member["organization_id"])
        assert len(rows) == 1
        row = rows[0]
        assert row["action"] == "access.denied"
        assert row["entity_type"] == "access"
        assert row["user_id"] == member["user_id"]
        assert row["after_state"] == {"path": "/v1/superadmin/organizations"}
