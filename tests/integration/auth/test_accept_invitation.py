"""tests/integration/auth/test_accept_invitation.py

SDD: core/spec_module_00_superadmin.md "Flujo de Activacion de Cuenta"
     pasos 3-5 + core/sdd_03_api_contracts.md §1 "POST /auth/accept-invitation".
Implements: CA-00-03.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_session_factory

pytestmark = pytest.mark.asyncio


async def _organization_status(organization_id) -> str:
    # issue #42: adminprop_app ya no bypassea RLS -- la lectura cross-tenant
    # necesita SET LOCAL ROLE adminprop_superadmin explicito.
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        result = await session.execute(
            sa.text("SELECT status FROM organizations WHERE id = :id"),
            {"id": str(organization_id)},
        )
        return result.scalar_one()


class TestCA0003AcceptInvitation:
    """CA-00-03: al completar la activacion, la organizacion pasa a
    `active` y el owner queda logueado con rol `owner`."""

    async def test_ca_00_03_accept_invitation_activates_organization_and_logs_in_owner(
        self, client, seed
    ):
        org_id = await seed.create_organization(status="pending_owner", name="Org Activacion")
        role_id = await seed.create_role(org_id, name="owner", permissions=["user:manage"])
        raw_token = await seed.create_invitation(
            organization_id=org_id, role_id=role_id, email="owner@example.com"
        )

        response = await client.post(
            "/v1/auth/accept-invitation",
            json={
                "token": raw_token,
                "full_name": "Juan Garcia",
                "password": "Password1234",
            },
        )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["status"] == "authenticated"
        assert data["user"]["email"] == "owner@example.com"
        assert data["organization"]["id"] == str(org_id)
        assert data["organization"]["role"] == "owner"
        assert response.headers.get_list("set-cookie") != []
        assert await _organization_status(org_id) == "active"

    async def test_accept_invitation_with_unknown_token_returns_invitation_not_found(self, client):
        response = await client.post(
            "/v1/auth/accept-invitation",
            json={
                "token": uuid.uuid4().hex,
                "full_name": "Juan Garcia",
                "password": "Password1234",
            },
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "INVITATION_NOT_FOUND"

    async def test_accept_invitation_with_expired_token_returns_invitation_expired(
        self, client, seed
    ):
        org_id = await seed.create_organization(status="pending_owner")
        role_id = await seed.create_role(org_id, name="owner")
        raw_token = await seed.create_invitation(
            organization_id=org_id, role_id=role_id, expires_in_hours=-1
        )

        response = await client.post(
            "/v1/auth/accept-invitation",
            json={
                "token": raw_token,
                "full_name": "Juan Garcia",
                "password": "Password1234",
            },
        )

        assert response.status_code == 410
        assert response.json()["error"]["code"] == "INVITATION_EXPIRED"

    async def test_accept_invitation_twice_returns_invitation_already_accepted(self, client, seed):
        org_id = await seed.create_organization(status="pending_owner")
        role_id = await seed.create_role(org_id, name="owner")
        raw_token = await seed.create_invitation(organization_id=org_id, role_id=role_id)

        first = await client.post(
            "/v1/auth/accept-invitation",
            json={"token": raw_token, "full_name": "Juan Garcia", "password": "Password1234"},
        )
        assert first.status_code == 201

        second = await client.post(
            "/v1/auth/accept-invitation",
            json={"token": raw_token, "full_name": "Juan Garcia", "password": "Password1234"},
        )

        assert second.status_code == 409
        assert second.json()["error"]["code"] == "INVITATION_ALREADY_ACCEPTED"

    async def test_accept_invitation_with_weak_password_returns_validation_error(
        self, client, seed
    ):
        org_id = await seed.create_organization(status="pending_owner")
        role_id = await seed.create_role(org_id, name="owner")
        raw_token = await seed.create_invitation(organization_id=org_id, role_id=role_id)

        response = await client.post(
            "/v1/auth/accept-invitation",
            json={"token": raw_token, "full_name": "Juan Garcia", "password": "short1"},
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_accept_invitation_with_password_missing_uppercase_returns_validation_error(
        self, client, seed
    ):
        org_id = await seed.create_organization(status="pending_owner")
        role_id = await seed.create_role(org_id, name="owner")
        raw_token = await seed.create_invitation(organization_id=org_id, role_id=role_id)

        response = await client.post(
            "/v1/auth/accept-invitation",
            json={"token": raw_token, "full_name": "Juan Garcia", "password": "password1234"},
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_accept_invitation_with_password_missing_digit_returns_validation_error(
        self, client, seed
    ):
        org_id = await seed.create_organization(status="pending_owner")
        role_id = await seed.create_role(org_id, name="owner")
        raw_token = await seed.create_invitation(organization_id=org_id, role_id=role_id)

        response = await client.post(
            "/v1/auth/accept-invitation",
            json={"token": raw_token, "full_name": "Juan Garcia", "password": "Passwordxxxx"},
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_accept_invitation_reuses_existing_global_user_without_membership(
        self, client, seed
    ):
        """El email de la invitacion ya existe como user global (de otra
        organizacion) pero sin membresia en ESTA org -- se reutiliza el
        user, no se crea uno nuevo (decision de implementacion del issue #8)."""
        existing = await seed.create_user(password="OldPass1234")
        org_id = await seed.create_organization(status="pending_owner")
        role_id = await seed.create_role(org_id, name="owner")
        raw_token = await seed.create_invitation(
            organization_id=org_id, role_id=role_id, email=existing["email"]
        )

        response = await client.post(
            "/v1/auth/accept-invitation",
            json={
                "token": raw_token,
                "full_name": "Nombre Ignorado Porque El User Ya Existe",
                "password": "Password1234",
            },
        )

        assert response.status_code == 201
        assert response.json()["data"]["user"]["id"] == str(existing["id"])

    async def test_accept_invitation_when_already_member_returns_user_already_member(
        self, client, seed
    ):
        member = await seed.create_active_member_with_org()
        raw_token = await seed.create_invitation(
            organization_id=member["organization_id"],
            role_id=member["role_id"],
            email=member["email"],
        )

        response = await client.post(
            "/v1/auth/accept-invitation",
            json={
                "token": raw_token,
                "full_name": "Ya Es Miembro",
                "password": "Password1234",
            },
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "USER_ALREADY_MEMBER"

    async def test_accept_invitation_rejects_unknown_fields(self, client, seed):
        org_id = await seed.create_organization(status="pending_owner")
        role_id = await seed.create_role(org_id, name="owner")
        raw_token = await seed.create_invitation(organization_id=org_id, role_id=role_id)

        response = await client.post(
            "/v1/auth/accept-invitation",
            json={
                "token": raw_token,
                "full_name": "Juan Garcia",
                "password": "Password1234",
                "role": "owner",
            },
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
