"""Tests de POST /v1/auth/login -- membresia activa y seleccion multi-org (issue #6).

SDD: docs/skills/tenant-isolation.md "Validar que el JWT corresponde a un
miembro activo del tenant". core/sdd_03_api_contracts.md parrafo 1.
"""

import uuid

import pytest

pytestmark = pytest.mark.asyncio


class TestLoginMembership:
    async def test_login_without_any_active_membership_returns_membership_inactive(
        self, client, seed
    ):
        user = await seed.create_user()

        response = await client.post(
            "/v1/auth/login", json={"email": user["email"], "password": user["password"]}
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "MEMBERSHIP_INACTIVE"

    async def test_login_with_inactive_membership_returns_membership_inactive(self, client, seed):
        user = await seed.create_user()
        org_id = await seed.create_organization()
        role_id = await seed.create_role(org_id)
        await seed.create_membership(
            user_id=user["id"], organization_id=org_id, role_id=role_id, status="inactive"
        )

        response = await client.post(
            "/v1/auth/login", json={"email": user["email"], "password": user["password"]}
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "MEMBERSHIP_INACTIVE"

    async def test_login_with_disabled_organization_returns_membership_inactive(self, client, seed):
        user = await seed.create_user()
        org_id = await seed.create_organization(status="disabled")
        role_id = await seed.create_role(org_id)
        await seed.create_membership(user_id=user["id"], organization_id=org_id, role_id=role_id)

        response = await client.post(
            "/v1/auth/login", json={"email": user["email"], "password": user["password"]}
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "MEMBERSHIP_INACTIVE"

    async def test_login_with_invalid_organization_id_returns_membership_inactive(
        self, client, seed
    ):
        member = await seed.create_active_member_with_org()

        response = await client.post(
            "/v1/auth/login",
            json={
                "email": member["email"],
                "password": member["password"],
                "organization_id": str(uuid.uuid4()),
            },
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "MEMBERSHIP_INACTIVE"


class TestLoginMultiOrganization:
    async def test_login_with_multiple_orgs_and_no_selection_requires_selection(self, client, seed):
        user = await seed.create_user()
        org_a = await seed.create_organization(name="Org A")
        org_b = await seed.create_organization(name="Org B")
        role_a = await seed.create_role(org_a)
        role_b = await seed.create_role(org_b)
        await seed.create_membership(user_id=user["id"], organization_id=org_a, role_id=role_a)
        await seed.create_membership(user_id=user["id"], organization_id=org_b, role_id=role_b)

        response = await client.post(
            "/v1/auth/login", json={"email": user["email"], "password": user["password"]}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["status"] == "organization_selection_required"
        # decision de implementacion: se incluye el usuario (util para UX
        # "Hola Juan, elegi una organizacion") aunque todavia no haya sesion.
        assert body["data"]["user"]["email"] == user["email"]
        assert len(body["data"]["organizations"]) == 2
        assert response.headers.get_list("set-cookie") == []
        # issue #84: sin JWT emitido todavia no hay organizacion resuelta --
        # permissions/is_super_admin van null (sdd_03 v1.6 §1).
        assert body["data"]["permissions"] is None
        assert body["data"]["is_super_admin"] is None

    async def test_login_with_multiple_orgs_and_explicit_selection_authenticates(
        self, client, seed
    ):
        user = await seed.create_user()
        org_a = await seed.create_organization(name="Org A")
        org_b = await seed.create_organization(name="Org B")
        role_a = await seed.create_role(org_a, permissions=["contract:manage"])
        role_b = await seed.create_role(org_b, permissions=["property:manage", "renter:read"])
        await seed.create_membership(user_id=user["id"], organization_id=org_a, role_id=role_a)
        await seed.create_membership(user_id=user["id"], organization_id=org_b, role_id=role_b)

        response = await client.post(
            "/v1/auth/login",
            json={
                "email": user["email"],
                "password": user["password"],
                "organization_id": str(org_b),
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["status"] == "authenticated"
        assert response.headers.get_list("set-cookie") != []
        # issue #84: permissions[] corresponden a la org ELEGIDA (org_b),
        # no a la primera membresia ni a org_a.
        assert sorted(body["data"]["permissions"]) == ["property:manage", "renter:read"]
        assert body["data"]["is_super_admin"] is False


class TestLoginRequestValidation:
    async def test_login_rejects_unknown_fields(self, client):
        response = await client.post(
            "/v1/auth/login",
            json={"email": "a@example.com", "password": "x", "admin_override": True},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_login_rejects_malformed_email(self, client):
        response = await client.post(
            "/v1/auth/login", json={"email": "not-an-email", "password": "x"}
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
