"""Tests de POST /v1/auth/refresh (issue #6).

SDD: core/sdd_03_api_contracts.md parrafo 1 -- "200 (rota refresh token;
cookie nueva)". core/sdd_04_nonfunctional.md parrafo 2.2 (rotativo
single-use, reuso revoca la familia completa).
"""

import re

import pytest

from adminprop.shared.auth.cookies import ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE

pytestmark = pytest.mark.asyncio


def _extract_cookie_value(set_cookie_headers: list[str], name: str) -> str:
    header = next(h for h in set_cookie_headers if h.startswith(f"{name}="))
    match = re.match(rf"{name}=([^;]+)", header)
    assert match is not None
    return match.group(1)


class TestRefreshHappyPath:
    async def test_refresh_rotates_tokens_and_returns_200(self, client, seed):
        member = await seed.create_active_member_with_org()
        login_response = await client.post(
            "/v1/auth/login", json={"email": member["email"], "password": member["password"]}
        )
        old_refresh = _extract_cookie_value(
            login_response.headers.get_list("set-cookie"), REFRESH_TOKEN_COOKIE
        )

        refresh_response = await client.post("/v1/auth/refresh")

        assert refresh_response.status_code == 200
        assert refresh_response.json()["data"]["status"] == "refreshed"
        new_cookies = refresh_response.headers.get_list("set-cookie")
        new_refresh = _extract_cookie_value(new_cookies, REFRESH_TOKEN_COOKIE)
        new_access = _extract_cookie_value(new_cookies, ACCESS_TOKEN_COOKIE)
        assert new_refresh != old_refresh
        assert new_access

    async def test_refresh_without_any_cookie_returns_401(self, client):
        response = await client.post("/v1/auth/refresh")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"


class TestRefreshRotationSingleUse:
    async def test_reusing_a_rotated_refresh_token_returns_401_and_revokes_family(
        self, client, seed
    ):
        member = await seed.create_active_member_with_org()
        login_response = await client.post(
            "/v1/auth/login", json={"email": member["email"], "password": member["password"]}
        )
        old_refresh = _extract_cookie_value(
            login_response.headers.get_list("set-cookie"), REFRESH_TOKEN_COOKIE
        )

        first_rotation = await client.post("/v1/auth/refresh")
        assert first_rotation.status_code == 200
        new_refresh = _extract_cookie_value(
            first_rotation.headers.get_list("set-cookie"), REFRESH_TOKEN_COOKIE
        )

        replay_response = await client.post(
            "/v1/auth/refresh", cookies={REFRESH_TOKEN_COOKIE: old_refresh}
        )
        assert replay_response.status_code == 401
        assert replay_response.json()["error"]["code"] == "UNAUTHORIZED"

        legit_next_response = await client.post(
            "/v1/auth/refresh", cookies={REFRESH_TOKEN_COOKIE: new_refresh}
        )
        assert legit_next_response.status_code == 401
        assert legit_next_response.json()["error"]["code"] == "UNAUTHORIZED"


class TestRefreshMembership:
    async def test_refresh_with_membership_deactivated_after_login_returns_membership_inactive(
        self, client, seed
    ):
        member = await seed.create_active_member_with_org()
        await client.post(
            "/v1/auth/login", json={"email": member["email"], "password": member["password"]}
        )

        # issue #42: adminprop_app ya no bypassea RLS -- la escritura
        # cross-tenant necesita SET LOCAL ROLE adminprop_superadmin
        # explicito.
        import sqlalchemy as sa

        from adminprop.db.session import get_session_factory

        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
            await session.execute(
                sa.text(
                    "UPDATE organization_members SET status = 'inactive' WHERE user_id = :user_id"
                ),
                {"user_id": str(member["id"])},
            )

        response = await client.post("/v1/auth/refresh")

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "MEMBERSHIP_INACTIVE"

    async def test_super_admin_refresh_reissues_super_admin_token(self, client, seed):
        user = await seed.create_user(is_super_admin=True)
        await client.post(
            "/v1/auth/login", json={"email": user["email"], "password": user["password"]}
        )

        response = await client.post("/v1/auth/refresh")

        assert response.status_code == 200

    async def test_refresh_with_super_admin_flag_revoked_returns_401(self, client, seed):
        """Cuenta que era super admin al hacer login pero perdio el flag
        antes del refresh -- no debe reemitirse un token super admin.
        """
        user = await seed.create_user(is_super_admin=True)
        await client.post(
            "/v1/auth/login", json={"email": user["email"], "password": user["password"]}
        )

        import sqlalchemy as sa

        from adminprop.db.session import get_session_factory

        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
            await session.execute(
                sa.text("UPDATE users SET is_super_admin = false WHERE id = :user_id"),
                {"user_id": str(user["id"])},
            )

        response = await client.post("/v1/auth/refresh")

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"


class TestLogoutRevocationSurvivesClientCookieClear:
    async def test_replaying_refresh_token_captured_before_logout_returns_401(self, client, seed):
        """Un atacante que copio el refresh token ANTES del logout no puede
        reusarlo despues -- revocacion server-side, no solo borrado de cookie.
        """
        member = await seed.create_active_member_with_org()
        login_response = await client.post(
            "/v1/auth/login", json={"email": member["email"], "password": member["password"]}
        )
        captured_refresh = _extract_cookie_value(
            login_response.headers.get_list("set-cookie"), REFRESH_TOKEN_COOKIE
        )

        logout_response = await client.post("/v1/auth/logout")
        assert logout_response.status_code == 204

        replay_response = await client.post(
            "/v1/auth/refresh", cookies={REFRESH_TOKEN_COOKIE: captured_refresh}
        )

        assert replay_response.status_code == 401
        assert replay_response.json()["error"]["code"] == "UNAUTHORIZED"
