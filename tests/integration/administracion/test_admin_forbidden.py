"""tests/integration/administracion/test_admin_forbidden.py

SDD: docs/sdd/features/spec_module_07_administracion.md CA-07-04 ("Un
admin recibe 403 FORBIDDEN al intentar invitar usuarios o cambiar la
configuracion; puede leer el log de auditoria").
"""

from __future__ import annotations

import pytest

from adminprop.modules.superadmin.provisioning import ADMIN_PERMISSIONS

pytestmark = pytest.mark.asyncio


async def _seed_org_with_admin(seed):
    org = await seed.create_organization_with_system_roles()
    admin = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["admin"],
        role_name="admin",
    )
    return org, admin


class TestCA0704AdminForbidden:
    async def test_ca_07_04_admin_cannot_invite_users(self, client, seed):
        _org, admin = await _seed_org_with_admin(seed)

        response = await client.post(
            "/v1/users/invite",
            json={"email": "nuevo@example.com", "role": "maintenance"},
            headers=admin["headers"],
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    async def test_ca_07_04_admin_cannot_update_organization_settings(self, client, seed):
        _org, admin = await _seed_org_with_admin(seed)

        response = await client.put(
            "/v1/organization/settings",
            json={
                "grace_day": 15,
                "contract_expiry_notice_days": 60,
                "billing_name": None,
                "billing_cuit": None,
                "billing_contact": None,
            },
            headers=admin["headers"],
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    async def test_ca_07_04_admin_cannot_list_or_manage_users(self, client, seed):
        _org, admin = await _seed_org_with_admin(seed)

        response = await client.get("/v1/users", headers=admin["headers"])

        assert response.status_code == 403

    async def test_ca_07_04_admin_cannot_read_roles(self, client, seed):
        """`role:read` tampoco esta en `ADMIN_PERMISSIONS` (sdd_03
        §"Resumen de Autorizacion por Recurso": "Usuarios, roles,
        configuracion de la org" es exclusivo del owner)."""
        _org, admin = await _seed_org_with_admin(seed)

        response = await client.get("/v1/roles", headers=admin["headers"])

        assert response.status_code == 403

    def test_ca_07_04_admin_retains_audit_read_permission(self):
        """CA-07-04: "puede leer el log de auditoria" -- el endpoint real
        de `GET /audit-logs` es del issue #32 (RF-05, fuera de alcance de
        este issue) y todavia no existe. Este test verifica la base que
        ya esta sembrada: `audit:read` esta presente en `ADMIN_PERMISSIONS`
        (`modules/superadmin/provisioning.py`), lista para que el
        endpoint del issue #32 la consuma con
        `Depends(requires_permission("audit:read"))`."""
        assert "audit:read" in ADMIN_PERMISSIONS
