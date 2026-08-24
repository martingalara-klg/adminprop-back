"""Tests de GET /v1/auth/me (issue #84).

SDD: core/sdd_03_api_contracts.md v1.6 §1 -- "GET /auth/me -> 200 { data:
{ user, organization, role, permissions[], is_super_admin } } | 401".
Sirve para rehidratar la sesion del front al recargar la pagina (el JWT
vive en cookie HttpOnly, decision #20, el cliente no puede leerlo).
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_session_factory

pytestmark = pytest.mark.asyncio


async def _deactivate_membership(organization_id, user_id) -> None:
    """Simula que la membresia se desactivo despues de emitido el JWT
    (mismo patron que `test_accept_invitation.py._organization_status`:
    `adminprop_app` ya no bypassea RLS, issue #42)."""
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        await session.execute(
            sa.text(
                "UPDATE organization_members SET status = 'inactive' "
                "WHERE organization_id = :org_id AND user_id = :user_id"
            ),
            {"org_id": str(organization_id), "user_id": str(user_id)},
        )


class TestMeWithValidSession:
    """CA-84-03: GET /auth/me con sesion valida devuelve la sesion vigente."""

    async def test_ca_84_03_me_with_valid_org_session_returns_current_session(self, client, seed):
        permissions = ["contract:manage", "property:read"]
        member = await seed.create_active_member_with_org(
            role_name="admin", permissions=permissions
        )
        await client.post(
            "/v1/auth/login", json={"email": member["email"], "password": member["password"]}
        )

        response = await client.get("/v1/auth/me")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["user"]["email"] == member["email"]
        assert data["organization"]["id"] == str(member["organization_id"])
        assert data["role"] == "admin"
        assert sorted(data["permissions"]) == sorted(permissions)
        assert data["is_super_admin"] is False

    async def test_ca_84_03_me_with_super_admin_session_returns_null_organization(
        self, client, super_admin_headers
    ):
        response = await client.get("/v1/auth/me", headers=super_admin_headers)

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["organization"] is None
        assert data["role"] is None
        assert data["permissions"] == []
        assert data["is_super_admin"] is True

    async def test_ca_84_03_me_after_multi_org_selection_returns_the_chosen_organization(
        self, client, seed
    ):
        """Tras el flujo multi-org (login con `organization_id` explicito),
        `/auth/me` refleja la organizacion realmente elegida -- no otra
        membresia del usuario."""
        user = await seed.create_user()
        org_a = await seed.create_organization(name="Org A")
        org_b = await seed.create_organization(name="Org B")
        role_a = await seed.create_role(org_a, permissions=["contract:manage"])
        role_b = await seed.create_role(org_b, permissions=["property:manage"])
        await seed.create_membership(user_id=user["id"], organization_id=org_a, role_id=role_a)
        await seed.create_membership(user_id=user["id"], organization_id=org_b, role_id=role_b)

        await client.post(
            "/v1/auth/login",
            json={
                "email": user["email"],
                "password": user["password"],
                "organization_id": str(org_b),
            },
        )

        response = await client.get("/v1/auth/me")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["organization"]["id"] == str(org_b)
        assert data["permissions"] == ["property:manage"]


class TestMeWithoutSession:
    """CA-84-03: sin sesion valida -> 401 estandar."""

    async def test_ca_84_03_me_without_any_cookie_returns_401(self, client):
        response = await client.get("/v1/auth/me")

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"

    async def test_ca_84_03_me_with_malformed_token_returns_401(self, client):
        response = await client.get(
            "/v1/auth/me", headers={"Authorization": "Bearer not-a-real-jwt"}
        )

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"

    async def test_ca_84_03_me_after_logout_returns_401(self, client, seed):
        member = await seed.create_active_member_with_org()
        await client.post(
            "/v1/auth/login", json={"email": member["email"], "password": member["password"]}
        )
        await client.post("/v1/auth/logout")

        response = await client.get("/v1/auth/me")

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"


class TestMeWithDeactivatedMembership:
    """Membresia desactivada despues de emitido el JWT -> 403
    MEMBERSHIP_INACTIVE (misma regla que valida `login`/`refresh`)."""

    async def test_me_with_membership_deactivated_after_login_returns_membership_inactive(
        self, client, seed
    ):
        member = await seed.create_active_member_with_org()
        await client.post(
            "/v1/auth/login", json={"email": member["email"], "password": member["password"]}
        )
        await _deactivate_membership(member["organization_id"], member["id"])

        response = await client.get("/v1/auth/me")

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "MEMBERSHIP_INACTIVE"


class TestMeReflectsLivePermissions:
    """`permissions[]` se resuelve en vivo contra la membresia actual, no
    contra el contenido cacheado del JWT."""

    async def test_me_reflects_role_permissions_changed_after_jwt_was_issued(self, client, seed):
        org_id = await seed.create_organization()
        role_id = await seed.create_role(org_id, name="admin", permissions=["contract:manage"])
        user = await seed.create_user()
        await seed.create_membership(user_id=user["id"], organization_id=org_id, role_id=role_id)
        await client.post(
            "/v1/auth/login", json={"email": user["email"], "password": user["password"]}
        )

        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
            await session.execute(
                sa.text(
                    "UPDATE roles SET permissions = :permissions WHERE id = :role_id"
                ).bindparams(sa.bindparam("permissions", type_=sa.JSON)),
                {"role_id": str(role_id), "permissions": '["contract:manage", "payment:create"]'},
            )

        response = await client.get("/v1/auth/me")

        assert response.status_code == 200
        assert sorted(response.json()["data"]["permissions"]) == [
            "contract:manage",
            "payment:create",
        ]
