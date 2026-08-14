"""tests/integration/superadmin/test_audit_trail.py

SDD: core/sdd_02_domain_model.md §2.17 + core/spec_module_00_superadmin.md RN-05.
Implements: CA-10-02 -- resuelve los TODO(#10) de
`modules/superadmin/service.py` (org.created, invitation.sent,
org.disabled, org.enabled).
"""

from __future__ import annotations

from uuid import UUID

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


class TestOrganizationCreatedAudited:
    async def test_create_organization_writes_org_created_audit_row(
        self, client, super_admin_headers
    ):
        response = await client.post(
            "/v1/superadmin/organizations",
            json={"name": "Acme Propiedades", "timezone": "America/Argentina/Cordoba"},
            headers=super_admin_headers,
        )
        assert response.status_code == 201
        organization_id = response.json()["data"]["id"]

        rows = await _audit_rows(organization_id, "org.created")
        assert len(rows) == 1
        assert rows[0]["entity_id"] == UUID(organization_id)
        assert rows[0]["after_state"]["status"] == "pending_owner"


class TestInvitationSentAudited:
    async def test_invite_owner_writes_invitation_sent_audit_row(
        self, client, super_admin_headers, sent_emails
    ):
        create_response = await client.post(
            "/v1/superadmin/organizations",
            json={"name": "Beta Propiedades", "timezone": "America/Argentina/Cordoba"},
            headers=super_admin_headers,
        )
        organization_id = create_response.json()["data"]["id"]

        response = await client.post(
            f"/v1/superadmin/organizations/{organization_id}/invite-owner",
            json={"email": "owner@example.com"},
            headers=super_admin_headers,
        )
        assert response.status_code == 201

        rows = await _audit_rows(organization_id, "invitation.sent")
        assert len(rows) == 1
        assert rows[0]["after_state"] == {"email": "owner@example.com", "role": "owner"}

    async def test_resend_invitation_writes_a_second_invitation_sent_audit_row(
        self, client, super_admin_headers, sent_emails
    ):
        create_response = await client.post(
            "/v1/superadmin/organizations",
            json={"name": "Gamma Propiedades", "timezone": "America/Argentina/Cordoba"},
            headers=super_admin_headers,
        )
        organization_id = create_response.json()["data"]["id"]
        await client.post(
            f"/v1/superadmin/organizations/{organization_id}/invite-owner",
            json={"email": "owner@example.com"},
            headers=super_admin_headers,
        )

        response = await client.post(
            f"/v1/superadmin/organizations/{organization_id}/resend-invitation",
            headers=super_admin_headers,
        )
        assert response.status_code == 201

        rows = await _audit_rows(organization_id, "invitation.sent")
        assert len(rows) == 2


class TestOrganizationDisabledEnabledAudited:
    async def test_disable_writes_org_disabled_audit_row_with_reason(
        self, client, super_admin_headers
    ):
        create_response = await client.post(
            "/v1/superadmin/organizations",
            json={"name": "Delta Propiedades", "timezone": "America/Argentina/Cordoba"},
            headers=super_admin_headers,
        )
        organization_id = create_response.json()["data"]["id"]

        response = await client.post(
            f"/v1/superadmin/organizations/{organization_id}/disable",
            json={"reason": "Falta de pago"},
            headers=super_admin_headers,
        )
        assert response.status_code == 200

        rows = await _audit_rows(organization_id, "org.disabled")
        assert len(rows) == 1
        assert rows[0]["before_state"] == {"status": "pending_owner"}
        assert rows[0]["after_state"] == {"status": "disabled", "reason": "Falta de pago"}

    async def test_enable_writes_org_enabled_audit_row_with_reason(
        self, client, super_admin_headers
    ):
        create_response = await client.post(
            "/v1/superadmin/organizations",
            json={"name": "Epsilon Propiedades", "timezone": "America/Argentina/Cordoba"},
            headers=super_admin_headers,
        )
        organization_id = create_response.json()["data"]["id"]
        await client.post(
            f"/v1/superadmin/organizations/{organization_id}/disable",
            json={"reason": "Falta de pago"},
            headers=super_admin_headers,
        )

        response = await client.post(
            f"/v1/superadmin/organizations/{organization_id}/enable",
            json={"reason": "Pago regularizado"},
            headers=super_admin_headers,
        )
        assert response.status_code == 200

        rows = await _audit_rows(organization_id, "org.enabled")
        assert len(rows) == 1
        assert rows[0]["before_state"] == {"status": "disabled"}
        assert rows[0]["after_state"] == {"status": "active", "reason": "Pago regularizado"}
