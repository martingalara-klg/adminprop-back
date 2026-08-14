"""Tests de POST /v1/auth/logout (issue #6).

SDD: core/sdd_03_api_contracts.md parrafo 1 -- "204 (invalida refresh
server-side, limpia cookies)".
"""

import pytest

from adminprop.shared.auth.cookies import ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE

pytestmark = pytest.mark.asyncio


class TestLogout:
    async def test_logout_after_login_returns_204_and_clears_cookies(self, client, seed):
        member = await seed.create_active_member_with_org()
        await client.post(
            "/v1/auth/login", json={"email": member["email"], "password": member["password"]}
        )

        response = await client.post("/v1/auth/logout")

        assert response.status_code == 204
        set_cookie_headers = response.headers.get_list("set-cookie")
        assert any(
            h.startswith(ACCESS_TOKEN_COOKIE) and ("Max-Age=0" in h or "max-age=0" in h.lower())
            for h in set_cookie_headers
        )
        assert any(
            h.startswith(REFRESH_TOKEN_COOKIE) and ("Max-Age=0" in h or "max-age=0" in h.lower())
            for h in set_cookie_headers
        )

    async def test_logout_without_any_session_still_returns_204(self, client):
        """Idempotente: sin cookie de refresh (ya deslogueado) igual responde 204."""
        response = await client.post("/v1/auth/logout")
        assert response.status_code == 204

    async def test_refresh_token_revoked_by_logout_cannot_be_reused(self, client, seed):
        member = await seed.create_active_member_with_org()
        await client.post(
            "/v1/auth/login", json={"email": member["email"], "password": member["password"]}
        )

        logout_response = await client.post("/v1/auth/logout")
        assert logout_response.status_code == 204

        refresh_response = await client.post("/v1/auth/refresh")

        assert refresh_response.status_code == 401
        assert refresh_response.json()["error"]["code"] == "UNAUTHORIZED"
