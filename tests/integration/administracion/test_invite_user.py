"""tests/integration/administracion/test_invite_user.py

SDD: docs/sdd/features/spec_module_07_administracion.md RF-01.
core/sdd_03_api_contracts.md §3 "POST /users/invite".
Implements: CA-07-01 (parcial -- la invitacion en si; el flujo end-to-end
completo de activacion esta en test_accept_invitation_flow.py).
"""

from __future__ import annotations

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


class TestCA0701InviteUser:
    async def test_ca_07_01_owner_invites_maintenance(self, client, seed, sent_emails):
        """CA-07-01: "El owner invita a un usuario con rol maintenance"."""
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.post(
            "/v1/users/invite",
            json={"email": "nuevo@example.com", "role": "maintenance"},
            headers=owner["headers"],
        )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["email"] == "nuevo@example.com"
        assert data["role"] == "maintenance"
        assert data["status"] == "pending"
        assert len(sent_emails) == 1
        assert sent_emails[0]["to"] == ["nuevo@example.com"]

    async def test_owner_invites_admin(self, client, seed, sent_emails):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.post(
            "/v1/users/invite",
            json={"email": "otro-admin@example.com", "role": "admin"},
            headers=owner["headers"],
        )

        assert response.status_code == 201
        assert response.json()["data"]["role"] == "admin"

    async def test_invite_rejects_owner_role(self, client, seed):
        """RF-01: "el rol owner solo se transfiere via Super Admin en
        MVP" -- `role="owner"` es rechazado por el Pydantic `Literal`
        con 400 VALIDATION_ERROR (sdd_03 §"Codigos de Error Globales":
        `VALIDATION_ERROR` es 400 en este proyecto), nunca llega al
        service."""
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.post(
            "/v1/users/invite",
            json={"email": "nuevo-owner@example.com", "role": "owner"},
            headers=owner["headers"],
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_invite_rejects_malformed_email(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.post(
            "/v1/users/invite",
            json={"email": "no-es-un-email", "role": "maintenance"},
            headers=owner["headers"],
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_invite_existing_member_returns_user_already_member(
        self, client, seed, sent_emails
    ):
        """RF-01: "Duplicados: email ya miembro -> 409 USER_ALREADY_MEMBER"."""
        org, owner = await _seed_org_with_owner(seed)
        member = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["maintenance"],
            role_name="maintenance",
        )

        response = await client.post(
            "/v1/users/invite",
            json={"email": member["email"], "role": "admin"},
            headers=owner["headers"],
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "USER_ALREADY_MEMBER"

    async def test_invite_existing_inactive_member_returns_user_already_member(self, client, seed):
        """USER_ALREADY_MEMBER tambien aplica a membresias `inactive`
        (no solo `active`) -- mismo criterio que accept-invitation."""
        org, owner = await _seed_org_with_owner(seed)
        member = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["maintenance"],
            role_name="maintenance",
            status="inactive",
        )

        response = await client.post(
            "/v1/users/invite",
            json={"email": member["email"], "role": "admin"},
            headers=owner["headers"],
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "USER_ALREADY_MEMBER"

    async def test_invite_pending_duplicate_returns_invitation_pending_exists(
        self, client, seed, sent_emails
    ):
        """RF-01: "ya invitado pendiente -> 409 INVITATION_PENDING_EXISTS"."""
        _org, owner = await _seed_org_with_owner(seed)

        first = await client.post(
            "/v1/users/invite",
            json={"email": "duplicado@example.com", "role": "maintenance"},
            headers=owner["headers"],
        )
        assert first.status_code == 201

        second = await client.post(
            "/v1/users/invite",
            json={"email": "duplicado@example.com", "role": "admin"},
            headers=owner["headers"],
        )

        assert second.status_code == 409
        assert second.json()["error"]["code"] == "INVITATION_PENDING_EXISTS"

    async def test_invite_allows_multiple_pending_invitations_to_different_emails(
        self, client, seed, sent_emails
    ):
        """A diferencia de `modules/superadmin` (una sola pending de
        owner por organizacion), aca conviven varias invitaciones
        pending a distintos emails en la misma organizacion."""
        _org, owner = await _seed_org_with_owner(seed)

        first = await client.post(
            "/v1/users/invite",
            json={"email": "a@example.com", "role": "maintenance"},
            headers=owner["headers"],
        )
        second = await client.post(
            "/v1/users/invite",
            json={"email": "b@example.com", "role": "admin"},
            headers=owner["headers"],
        )

        assert first.status_code == 201
        assert second.status_code == 201
