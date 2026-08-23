"""Tests de POST /v1/auth/login (issue #6) -- happy path, anti-enumeration, lockout.

SDD: core/sdd_03_api_contracts.md parrafo 1 "Autenticacion".
core/sdd_04_nonfunctional.md parrafo 2.1/2.2/2.2a/2.3.
"""

import pytest

from adminprop.shared.auth.cookies import ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE

pytestmark = pytest.mark.asyncio


class TestLoginHappyPath:
    async def test_login_with_valid_credentials_returns_200_authenticated(self, client, seed):
        member = await seed.create_active_member_with_org()

        response = await client.post(
            "/v1/auth/login", json={"email": member["email"], "password": member["password"]}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["status"] == "authenticated"
        assert body["data"]["user"]["email"] == member["email"]
        assert body["data"]["organizations"][0]["id"] == str(member["organization_id"])
        assert body["data"]["organizations"][0]["role"] == "owner"

    async def test_login_sets_httponly_secure_samesite_lax_cookies(self, client, seed):
        member = await seed.create_active_member_with_org()

        response = await client.post(
            "/v1/auth/login", json={"email": member["email"], "password": member["password"]}
        )

        set_cookie_headers = response.headers.get_list("set-cookie")
        assert len(set_cookie_headers) == 2
        for header in set_cookie_headers:
            assert "httponly" in header.lower()
            assert "secure" in header.lower()
            assert "samesite=lax" in header.lower()
        assert any(h.startswith(ACCESS_TOKEN_COOKIE) for h in set_cookie_headers)
        assert any(h.startswith(REFRESH_TOKEN_COOKIE) for h in set_cookie_headers)

    async def test_super_admin_login_returns_authenticated_without_organizations(
        self, client, seed
    ):
        user = await seed.create_user(is_super_admin=True)

        response = await client.post(
            "/v1/auth/login", json={"email": user["email"], "password": user["password"]}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["status"] == "authenticated"
        assert body["data"]["organizations"] == []


class TestLoginAntiEnumeration:
    async def test_login_with_nonexistent_email_returns_literal_message(self, client):
        response = await client.post(
            "/v1/auth/login", json={"email": "nadie@example.com", "password": "Password1234"}
        )

        assert response.status_code == 401
        body = response.json()
        assert body["error"]["code"] == "UNAUTHORIZED"
        assert body["error"]["message"] == "Credenciales incorrectas."

    async def test_login_with_wrong_password_returns_identical_literal_message(self, client, seed):
        member = await seed.create_active_member_with_org()

        response = await client.post(
            "/v1/auth/login", json={"email": member["email"], "password": "WrongPassword1"}
        )

        assert response.status_code == 401
        body = response.json()
        assert body["error"]["code"] == "UNAUTHORIZED"
        assert body["error"]["message"] == "Credenciales incorrectas."

    async def test_login_failure_does_not_set_any_cookie(self, client, seed):
        member = await seed.create_active_member_with_org()

        response = await client.post(
            "/v1/auth/login", json={"email": member["email"], "password": "WrongPassword1"}
        )

        assert response.headers.get_list("set-cookie") == []


class TestLoginLockout:
    async def test_sixth_failed_attempt_within_window_returns_account_locked(self, client, seed):
        member = await seed.create_active_member_with_org()

        for _ in range(5):
            response = await client.post(
                "/v1/auth/login", json={"email": member["email"], "password": "WrongPassword1"}
            )
            assert response.status_code == 401

        response = await client.post(
            "/v1/auth/login", json={"email": member["email"], "password": member["password"]}
        )

        assert response.status_code == 403
        body = response.json()
        assert body["error"]["code"] == "ACCOUNT_LOCKED"
        assert body["error"]["details"]["retry_after_seconds"] > 0
        assert body["error"]["details"]["retry_after_seconds"] <= 30 * 60

    async def test_locked_account_rejects_even_correct_password(self, client, seed):
        member = await seed.create_active_member_with_org()
        for _ in range(5):
            await client.post(
                "/v1/auth/login", json={"email": member["email"], "password": "WrongPassword1"}
            )

        response = await client.post(
            "/v1/auth/login", json={"email": member["email"], "password": member["password"]}
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "ACCOUNT_LOCKED"

    async def test_successful_login_resets_failure_counter(self, client, seed):
        member = await seed.create_active_member_with_org()
        for _ in range(4):
            await client.post(
                "/v1/auth/login", json={"email": member["email"], "password": "WrongPassword1"}
            )

        ok_response = await client.post(
            "/v1/auth/login", json={"email": member["email"], "password": member["password"]}
        )
        assert ok_response.status_code == 200

        for _ in range(4):
            response = await client.post(
                "/v1/auth/login", json={"email": member["email"], "password": "WrongPassword1"}
            )
            assert response.status_code == 401
