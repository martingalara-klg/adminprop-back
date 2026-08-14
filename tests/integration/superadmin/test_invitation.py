"""tests/integration/superadmin/test_invitation.py

SDD: core/spec_module_00_superadmin.md RF-03, RF-04.
"""

from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.asyncio


async def _create_org(client, super_admin_headers, name: str = "Org Invitacion") -> str:
    response = await client.post(
        "/v1/superadmin/organizations", json={"name": name}, headers=super_admin_headers
    )
    return response.json()["data"]["id"]


class TestCA0002InviteOwner:
    """CA-00-02: La invitacion de owner llega por email y expira a las 72h;
    el Super Admin puede reenviar (la anterior queda `revoked`)."""

    async def test_ca_00_02_invite_owner_returns_pending_invitation_expiring_in_72h(
        self, client, super_admin_headers, sent_emails
    ):
        org_id = await _create_org(client, super_admin_headers)

        response = await client.post(
            f"/v1/superadmin/organizations/{org_id}/invite-owner",
            json={"email": "owner@example.com"},
            headers=super_admin_headers,
        )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["email"] == "owner@example.com"
        assert data["status"] == "pending"

        expires_at = datetime.fromisoformat(data["expires_at"])
        hours_until_expiry = (expires_at - datetime.now(UTC)).total_seconds() / 3600
        assert 71 < hours_until_expiry <= 72

    async def test_ca_00_02_invite_owner_enqueues_transactional_email(
        self, client, super_admin_headers, sent_emails
    ):
        org_id = await _create_org(client, super_admin_headers, name="Org Con Email")

        await client.post(
            f"/v1/superadmin/organizations/{org_id}/invite-owner",
            json={"email": "owner2@example.com"},
            headers=super_admin_headers,
        )

        assert len(sent_emails) == 1
        assert sent_emails[0]["to"] == ["owner2@example.com"]
        assert "accept-invitation?token=" in sent_emails[0]["html"]

    async def test_second_invite_owner_call_returns_invitation_pending_exists(
        self, client, super_admin_headers, sent_emails
    ):
        """RF-03: una sola invitacion de owner `pending` por organizacion --
        `invite-owner` (a diferencia de `resend-invitation`) no revoca la
        anterior automaticamente."""
        org_id = await _create_org(client, super_admin_headers, name="Org Doble Invite")
        await client.post(
            f"/v1/superadmin/organizations/{org_id}/invite-owner",
            json={"email": "first@example.com"},
            headers=super_admin_headers,
        )

        response = await client.post(
            f"/v1/superadmin/organizations/{org_id}/invite-owner",
            json={"email": "second@example.com"},
            headers=super_admin_headers,
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INVITATION_PENDING_EXISTS"

    async def test_invite_owner_on_nonexistent_organization_returns_404(
        self, client, super_admin_headers
    ):
        response = await client.post(
            "/v1/superadmin/organizations/00000000-0000-0000-0000-000000000000/invite-owner",
            json={"email": "owner@example.com"},
            headers=super_admin_headers,
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_invite_owner_on_non_pending_organization_returns_validation_error(
        self, client, super_admin_headers, sent_emails
    ):
        """Solo se invita al owner mientras la organizacion sigue `pending_owner`
        -- una vez `disabled` (o `active`, issue #8) ya no acepta invite-owner."""
        org_id = await _create_org(client, super_admin_headers, name="Org No Pending")
        await client.post(
            f"/v1/superadmin/organizations/{org_id}/invite-owner",
            json={"email": "owner@example.com"},
            headers=super_admin_headers,
        )
        await client.post(
            f"/v1/superadmin/organizations/{org_id}/disable",
            json={"reason": "forzar transicion de estado para el test"},
            headers=super_admin_headers,
        )

        response = await client.post(
            f"/v1/superadmin/organizations/{org_id}/invite-owner",
            json={"email": "otro@example.com"},
            headers=super_admin_headers,
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_invalid_email_format_returns_validation_error(
        self, client, super_admin_headers
    ):
        org_id = await _create_org(client, super_admin_headers, name="Org Email Invalido")

        response = await client.post(
            f"/v1/superadmin/organizations/{org_id}/invite-owner",
            json={"email": "not-an-email"},
            headers=super_admin_headers,
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


class TestCA0002ResendInvitation:
    """CA-00-02: reenviar regenera token/expiracion; la invitacion anterior
    queda `revoked` (RF-04)."""

    async def test_resend_invitation_revokes_previous_and_issues_new_token(
        self, client, super_admin_headers, sent_emails
    ):
        org_id = await _create_org(client, super_admin_headers, name="Org Reenvio")
        first = await client.post(
            f"/v1/superadmin/organizations/{org_id}/invite-owner",
            json={"email": "owner@example.com"},
            headers=super_admin_headers,
        )
        first_invitation_id = first.json()["data"]["id"]

        second = await client.post(
            f"/v1/superadmin/organizations/{org_id}/resend-invitation",
            headers=super_admin_headers,
        )

        assert second.status_code == 201
        second_data = second.json()["data"]
        assert second_data["email"] == "owner@example.com"
        assert second_data["status"] == "pending"
        assert second_data["id"] != first_invitation_id
        assert len(sent_emails) == 2

    async def test_resend_invitation_on_nonexistent_organization_returns_404(
        self, client, super_admin_headers
    ):
        response = await client.post(
            "/v1/superadmin/organizations/00000000-0000-0000-0000-000000000000/resend-invitation",
            headers=super_admin_headers,
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_resend_invitation_without_pending_invitation_returns_404(
        self, client, super_admin_headers
    ):
        org_id = await _create_org(client, super_admin_headers, name="Org Sin Invitacion")

        response = await client.post(
            f"/v1/superadmin/organizations/{org_id}/resend-invitation",
            headers=super_admin_headers,
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
