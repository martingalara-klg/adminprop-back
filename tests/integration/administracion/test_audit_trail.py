"""tests/integration/administracion/test_audit_trail.py

SDD: core/sdd_02_domain_model.md §2.17 + §3 RN-D04.
Implements: CA-10-02 (AuditService usable por administracion) --
resuelve los TODO(#10) de `modules/administracion/service.py`
(user.role_changed, user.deactivated, settings.changed).
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_session_factory

pytestmark = pytest.mark.asyncio


async def _audit_rows(organization_id, action: str) -> list[dict]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            sa.text(
                "SELECT entity_id, user_id, before_state, after_state FROM audit_logs "
                "WHERE organization_id = :org_id AND action = :action ORDER BY created_at"
            ),
            {"org_id": str(organization_id), "action": action},
        )
        return [dict(row._mapping) for row in result]


async def _seed_org_with_owner(seed):
    org = await seed.create_organization_with_system_roles()
    owner = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["owner"],
        role_name="owner",
    )
    return org, owner


class TestUserRoleChangedAudited:
    async def test_change_role_writes_user_role_changed_audit_row(self, client, seed):
        org, owner = await _seed_org_with_owner(seed)
        admin = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["admin"],
            role_name="admin",
        )

        response = await client.patch(
            f"/v1/users/{admin['id']}",
            json={"role": "maintenance"},
            headers=owner["headers"],
        )
        assert response.status_code == 200

        rows = await _audit_rows(org["organization_id"], "user.role_changed")
        assert len(rows) == 1
        assert rows[0]["entity_id"] == admin["id"]
        assert rows[0]["user_id"] == owner["id"]
        assert rows[0]["before_state"] == {"role": "admin"}
        assert rows[0]["after_state"] == {"role": "maintenance"}


class TestUserDeactivatedAudited:
    async def test_deactivate_writes_user_deactivated_audit_row(self, client, seed):
        org, owner = await _seed_org_with_owner(seed)
        admin = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["admin"],
            role_name="admin",
        )

        response = await client.delete(f"/v1/users/{admin['id']}", headers=owner["headers"])
        assert response.status_code == 204

        rows = await _audit_rows(org["organization_id"], "user.deactivated")
        assert len(rows) == 1
        assert rows[0]["entity_id"] == admin["id"]
        assert rows[0]["user_id"] == owner["id"]
        assert rows[0]["before_state"] == {"status": "active"}
        assert rows[0]["after_state"] == {"status": "inactive"}


class TestSettingsChangedAudited:
    async def test_update_settings_writes_settings_changed_audit_row(self, client, seed):
        org, owner = await _seed_org_with_owner(seed)

        response = await client.put(
            "/v1/organization/settings",
            json={
                "grace_day": 20,
                "contract_expiry_notice_days": 90,
                "billing_name": "Acme",
                "billing_cuit": None,
                "billing_contact": None,
            },
            headers=owner["headers"],
        )
        assert response.status_code == 200

        rows = await _audit_rows(org["organization_id"], "settings.changed")
        assert len(rows) == 1
        assert rows[0]["user_id"] == owner["id"]
        assert rows[0]["before_state"]["grace_day"] == 10
        assert rows[0]["after_state"]["grace_day"] == 20

    async def test_update_settings_with_no_actual_change_does_not_audit(self, client, seed):
        """Decision de implementacion (issue #10): no generar ruido en
        `audit_logs` si el PUT no cambia nada. La primera llamada
        establece `billing_header` (antes ausente en el default sembrado
        -- cambia la estructura, se audita); la SEGUNDA llamada, con los
        mismos valores, es un verdadero no-op y no debe auditar de nuevo.
        """
        org, owner = await _seed_org_with_owner(seed)
        payload = {
            "grace_day": 10,
            "contract_expiry_notice_days": 60,
            "billing_name": None,
            "billing_cuit": None,
            "billing_contact": None,
        }

        first = await client.put(
            "/v1/organization/settings", json=payload, headers=owner["headers"]
        )
        assert first.status_code == 200
        assert len(await _audit_rows(org["organization_id"], "settings.changed")) == 1

        second = await client.put(
            "/v1/organization/settings", json=payload, headers=owner["headers"]
        )
        assert second.status_code == 200

        rows = await _audit_rows(org["organization_id"], "settings.changed")
        assert len(rows) == 1
