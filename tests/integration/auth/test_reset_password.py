"""tests/integration/auth/test_reset_password.py

SDD: core/sdd_03_api_contracts.md §1 "GET /auth/reset-password/:token ->
200 | 404 | 410" + "POST /auth/reset-password -> 200". core/sdd_04_nonfunctional.md
§2.2 (refresh tokens revocables server-side).

Naming: estos flujos no tienen CA-XX formal en el issue #8 (a diferencia
de CA-00-03/04) -- se usan nombres descriptivos por criterio propio, tal
como habilita el issue.
"""

from __future__ import annotations

import re
import uuid

import pytest

from adminprop.config import get_settings
from adminprop.modules.auth import service as auth_service

pytestmark = pytest.mark.asyncio

_TOKEN_RE = re.compile(r"reset-password\?token=([^\"&\s]+)")


@pytest.fixture()
def sent_emails(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def _fake_delay(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(auth_service.send_transactional_email, "delay", _fake_delay)
    return calls


@pytest.fixture()
def expired_reset_token_settings(monkeypatch):
    """TTL logico negativo -> el token queda `expired=True` desde que se
    emite, sin dejar de existir (la ventana fisica de retencion sigue
    siendo la default, positiva) -- permite ejercitar 410 sin sleep()."""
    monkeypatch.setenv("PASSWORD_RESET_TOKEN_TTL_SECONDS", "-10")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _request_reset_token(client, sent_emails, email: str) -> str:
    response = await client.post("/v1/auth/forgot-password", json={"email": email})
    assert response.status_code == 200
    match = _TOKEN_RE.search(sent_emails[-1]["html"])
    assert match is not None
    return match.group(1)


class TestGetResetPasswordToken:
    async def test_get_reset_password_token_returns_email_for_valid_token(
        self, client, seed, sent_emails
    ):
        user = await seed.create_user()
        raw_token = await _request_reset_token(client, sent_emails, user["email"])

        response = await client.get(f"/v1/auth/reset-password/{raw_token}")

        assert response.status_code == 200
        assert response.json()["data"]["email"] == user["email"]

    async def test_get_reset_password_token_with_unknown_token_returns_404(self, client):
        response = await client.get(f"/v1/auth/reset-password/{uuid.uuid4().hex}")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_get_reset_password_token_with_expired_token_returns_reset_token_expired(
        self, client, seed, sent_emails, expired_reset_token_settings
    ):
        user = await seed.create_user()
        raw_token = await _request_reset_token(client, sent_emails, user["email"])

        response = await client.get(f"/v1/auth/reset-password/{raw_token}")

        assert response.status_code == 410
        assert response.json()["error"]["code"] == "RESET_TOKEN_EXPIRED"


class TestResetPassword:
    async def test_reset_password_updates_password_and_rejects_old_password_on_login(
        self, client, seed, sent_emails
    ):
        user = await seed.create_user(password="OldPass1234")
        raw_token = await _request_reset_token(client, sent_emails, user["email"])

        response = await client.post(
            "/v1/auth/reset-password", json={"token": raw_token, "password": "NewPass5678"}
        )
        assert response.status_code == 200

        old_login = await client.post(
            "/v1/auth/login", json={"email": user["email"], "password": "OldPass1234"}
        )
        assert old_login.status_code == 401
        assert old_login.json()["error"]["code"] == "UNAUTHORIZED"

        # El user no tiene membresia (seed.create_user no crea org) -- el
        # login con la password NUEVA debe pasar la verificacion de
        # credenciales y fallar recien en el chequeo de membresia
        # (403 MEMBERSHIP_INACTIVE, no 401), confirmando que la password
        # nueva quedo persistida.
        new_login = await client.post(
            "/v1/auth/login", json={"email": user["email"], "password": "NewPass5678"}
        )
        assert new_login.status_code == 403
        assert new_login.json()["error"]["code"] == "MEMBERSHIP_INACTIVE"

    async def test_reset_password_revokes_existing_refresh_tokens(self, client, seed, sent_emails):
        member = await seed.create_active_member_with_org(password="OldPass1234")

        login = await client.post(
            "/v1/auth/login", json={"email": member["email"], "password": "OldPass1234"}
        )
        assert login.status_code == 200
        old_refresh_cookie = client.cookies.get("refresh_token")
        assert old_refresh_cookie is not None

        raw_token = await _request_reset_token(client, sent_emails, member["email"])
        reset = await client.post(
            "/v1/auth/reset-password", json={"token": raw_token, "password": "NewPass5678"}
        )
        assert reset.status_code == 200

        replay = await client.post(
            "/v1/auth/refresh", cookies={"refresh_token": old_refresh_cookie}
        )

        assert replay.status_code == 401
        assert replay.json()["error"]["code"] == "UNAUTHORIZED"

    async def test_reset_password_with_unknown_token_returns_404(self, client):
        response = await client.post(
            "/v1/auth/reset-password",
            json={"token": uuid.uuid4().hex, "password": "NewPass5678"},
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_reset_password_with_expired_token_returns_reset_token_expired(
        self, client, seed, sent_emails, expired_reset_token_settings
    ):
        user = await seed.create_user()
        raw_token = await _request_reset_token(client, sent_emails, user["email"])

        response = await client.post(
            "/v1/auth/reset-password", json={"token": raw_token, "password": "NewPass5678"}
        )

        assert response.status_code == 410
        assert response.json()["error"]["code"] == "RESET_TOKEN_EXPIRED"

    async def test_reset_password_token_is_single_use(self, client, seed, sent_emails):
        user = await seed.create_user()
        raw_token = await _request_reset_token(client, sent_emails, user["email"])

        first = await client.post(
            "/v1/auth/reset-password", json={"token": raw_token, "password": "NewPass5678"}
        )
        assert first.status_code == 200

        second = await client.post(
            "/v1/auth/reset-password", json={"token": raw_token, "password": "OtherPass999"}
        )

        assert second.status_code == 404
        assert second.json()["error"]["code"] == "NOT_FOUND"

    async def test_reset_password_rejects_weak_password(self, client, seed, sent_emails):
        user = await seed.create_user()
        raw_token = await _request_reset_token(client, sent_emails, user["email"])

        response = await client.post(
            "/v1/auth/reset-password", json={"token": raw_token, "password": "short1"}
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_reset_password_rejects_unknown_fields(self, client):
        response = await client.post(
            "/v1/auth/reset-password",
            json={"token": "x", "password": "Password1234", "email": "a@example.com"},
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
