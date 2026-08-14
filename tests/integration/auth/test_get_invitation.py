"""tests/integration/auth/test_get_invitation.py

SDD: core/spec_module_00_superadmin.md "Flujo de Activacion de Cuenta" paso 2
     + core/sdd_03_api_contracts.md §1 "GET /auth/invitation/:token".
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


class TestGetInvitation:
    """GET /v1/auth/invitation/:token -- valida el token antes de mostrar
    el formulario de activacion (paso 2 del flujo)."""

    async def test_get_invitation_with_pending_token_returns_email_org_role(self, client, seed):
        org_id = await seed.create_organization(status="pending_owner", name="Org Invitacion")
        role_id = await seed.create_role(org_id, name="owner", permissions=["user:manage"])
        raw_token = await seed.create_invitation(
            organization_id=org_id, role_id=role_id, email="owner@example.com"
        )

        response = await client.get(f"/v1/auth/invitation/{raw_token}")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["email"] == "owner@example.com"
        assert data["organization_name"] == "Org Invitacion"
        assert data["role_name"] == "owner"

    async def test_get_invitation_with_unknown_token_returns_invitation_not_found(self, client):
        response = await client.get(f"/v1/auth/invitation/{uuid.uuid4().hex}")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "INVITATION_NOT_FOUND"

    async def test_get_invitation_with_expired_token_returns_invitation_expired(self, client, seed):
        org_id = await seed.create_organization(status="pending_owner")
        role_id = await seed.create_role(org_id, name="owner")
        raw_token = await seed.create_invitation(
            organization_id=org_id, role_id=role_id, expires_in_hours=-1
        )

        response = await client.get(f"/v1/auth/invitation/{raw_token}")

        assert response.status_code == 410
        assert response.json()["error"]["code"] == "INVITATION_EXPIRED"

    async def test_get_invitation_with_already_accepted_token_returns_invitation_already_accepted(
        self, client, seed
    ):
        org_id = await seed.create_organization(status="pending_owner")
        role_id = await seed.create_role(org_id, name="owner")
        raw_token = await seed.create_invitation(
            organization_id=org_id, role_id=role_id, status="accepted"
        )

        response = await client.get(f"/v1/auth/invitation/{raw_token}")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INVITATION_ALREADY_ACCEPTED"

    async def test_get_invitation_with_revoked_token_returns_invitation_not_found(
        self, client, seed
    ):
        """Una invitacion `revoked` (reemplazada por un reenvio) no debe
        distinguirse de "no existe" -- no revela el ciclo de vida interno."""
        org_id = await seed.create_organization(status="pending_owner")
        role_id = await seed.create_role(org_id, name="owner")
        raw_token = await seed.create_invitation(
            organization_id=org_id, role_id=role_id, status="revoked"
        )

        response = await client.get(f"/v1/auth/invitation/{raw_token}")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "INVITATION_NOT_FOUND"
