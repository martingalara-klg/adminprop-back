"""tests/integration/administracion/test_list_invitations_resend_revoke.py

SDD: docs/sdd/features/spec_module_07_administracion.md RF-01 ("reenvio y
revocacion desde el listado de invitaciones pendientes"). core/sdd_03_api_contracts.md
§3 "GET /users/invitations", "POST /users/invitations/:id/resend",
"DELETE /users/invitations/:id".
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _seed_org_with_owner(seed):
    org = await seed.create_organization_with_system_roles()
    owner = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["owner"],
        role_name="owner",
    )
    return org, owner


class TestListInvitations:
    async def test_list_invitations_returns_only_pending(self, client, seed, sent_emails):
        """RF-01: "listado de invitaciones pendientes" (texto literal) --
        filtra `status='pending'`, no muestra revocadas."""
        _org, owner = await _seed_org_with_owner(seed)
        await client.post(
            "/v1/users/invite",
            json={"email": "pending@example.com", "role": "maintenance"},
            headers=owner["headers"],
        )
        revoked_response = await client.post(
            "/v1/users/invite",
            json={"email": "sera-revocada@example.com", "role": "admin"},
            headers=owner["headers"],
        )
        revoked_id = revoked_response.json()["data"]["id"]
        await client.delete(f"/v1/users/invitations/{revoked_id}", headers=owner["headers"])

        response = await client.get("/v1/users/invitations", headers=owner["headers"])

        assert response.status_code == 200
        emails = {item["email"] for item in response.json()["data"]}
        assert emails == {"pending@example.com"}

    async def test_list_invitations_paginates_with_cursor(self, client, seed, sent_emails):
        _org, owner = await _seed_org_with_owner(seed)
        for i in range(3):
            await client.post(
                "/v1/users/invite",
                json={"email": f"user{i}@example.com", "role": "maintenance"},
                headers=owner["headers"],
            )

        first_page = await client.get(
            "/v1/users/invitations", params={"limit": 2}, headers=owner["headers"]
        )
        assert first_page.status_code == 200
        assert len(first_page.json()["data"]) == 2
        next_cursor = first_page.json()["meta"]["next_cursor"]
        assert next_cursor is not None

        second_page = await client.get(
            "/v1/users/invitations",
            params={"limit": 2, "cursor": next_cursor},
            headers=owner["headers"],
        )
        assert second_page.status_code == 200
        assert len(second_page.json()["data"]) == 1


class TestResendInvitation:
    async def test_resend_invitation_revokes_old_and_issues_new(self, client, seed, sent_emails):
        _org, owner = await _seed_org_with_owner(seed)
        invite_response = await client.post(
            "/v1/users/invite",
            json={"email": "reenviar@example.com", "role": "maintenance"},
            headers=owner["headers"],
        )
        invitation_id = invite_response.json()["data"]["id"]

        resend_response = await client.post(
            f"/v1/users/invitations/{invitation_id}/resend", headers=owner["headers"]
        )

        assert resend_response.status_code == 201
        new_data = resend_response.json()["data"]
        assert new_data["email"] == "reenviar@example.com"
        assert new_data["id"] != invitation_id
        assert len(sent_emails) == 2

        # La invitacion anterior ya no aparece en el listado de pending.
        list_response = await client.get("/v1/users/invitations", headers=owner["headers"])
        ids = {item["id"] for item in list_response.json()["data"]}
        assert invitation_id not in ids
        assert new_data["id"] in ids

    async def test_resend_unknown_invitation_returns_404(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.post(
            f"/v1/users/invitations/{uuid.uuid4()}/resend", headers=owner["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_resend_already_revoked_invitation_returns_404(self, client, seed, sent_emails):
        _org, owner = await _seed_org_with_owner(seed)
        invite_response = await client.post(
            "/v1/users/invite",
            json={"email": "ya-revocada@example.com", "role": "maintenance"},
            headers=owner["headers"],
        )
        invitation_id = invite_response.json()["data"]["id"]
        await client.delete(f"/v1/users/invitations/{invitation_id}", headers=owner["headers"])

        response = await client.post(
            f"/v1/users/invitations/{invitation_id}/resend", headers=owner["headers"]
        )

        assert response.status_code == 404


class TestRevokeInvitation:
    async def test_revoke_invitation_returns_204(self, client, seed, sent_emails):
        _org, owner = await _seed_org_with_owner(seed)
        invite_response = await client.post(
            "/v1/users/invite",
            json={"email": "a-revocar@example.com", "role": "maintenance"},
            headers=owner["headers"],
        )
        invitation_id = invite_response.json()["data"]["id"]

        response = await client.delete(
            f"/v1/users/invitations/{invitation_id}", headers=owner["headers"]
        )

        assert response.status_code == 204

        list_response = await client.get("/v1/users/invitations", headers=owner["headers"])
        ids = {item["id"] for item in list_response.json()["data"]}
        assert invitation_id not in ids

    async def test_revoke_unknown_invitation_returns_404(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.delete(
            f"/v1/users/invitations/{uuid.uuid4()}", headers=owner["headers"]
        )

        assert response.status_code == 404
