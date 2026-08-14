"""tests/integration/auth/test_forgot_password.py

SDD: core/sdd_03_api_contracts.md §1 "POST /auth/forgot-password -> 200
SIEMPRE (anti-enumeration)" + core/sdd_04_nonfunctional.md §2.2a (texto
literal) + §2.5 (rate limit 5/IP/hora).
"""

from __future__ import annotations

import pytest

from adminprop.modules.auth import service as auth_service

pytestmark = pytest.mark.asyncio

# sdd_04 §2.2a -- texto literal, no traducir ni "mejorar".
_LITERAL_MESSAGE = (
    "Si el email está registrado, recibirás instrucciones para restablecer "
    "tu contraseña en los próximos minutos."
)


@pytest.fixture()
def sent_emails(monkeypatch) -> list[dict]:
    """Intercepta el encolado del email de reset -- nunca Resend real en
    tests (docs/skills/testing.md), mismo patron que
    tests/integration/superadmin/conftest.py "sent_emails"."""
    calls: list[dict] = []

    def _fake_delay(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(auth_service.send_transactional_email, "delay", _fake_delay)
    return calls


class TestForgotPassword:
    """sdd_04 §2.2a: la respuesta es identica exista o no el email."""

    async def test_forgot_password_with_registered_email_returns_200_and_enqueues_email(
        self, client, seed, sent_emails
    ):
        user = await seed.create_user()

        response = await client.post("/v1/auth/forgot-password", json={"email": user["email"]})

        assert response.status_code == 200
        assert response.json()["data"]["message"] == _LITERAL_MESSAGE
        assert len(sent_emails) == 1
        assert sent_emails[0]["to"] == [user["email"]]
        assert "reset-password?token=" in sent_emails[0]["html"]

    async def test_forgot_password_with_unregistered_email_returns_200_without_enqueueing(
        self, client, sent_emails
    ):
        """Anti-enumeration: mismo 200 + mismo texto, pero sin efecto real
        (no se crea token ni se envia email) -- verificado indirectamente
        via `sent_emails` vacio."""
        response = await client.post(
            "/v1/auth/forgot-password", json={"email": "no-existe@example.com"}
        )

        assert response.status_code == 200
        assert response.json()["data"]["message"] == _LITERAL_MESSAGE
        assert sent_emails == []

    async def test_forgot_password_rejects_malformed_email(self, client):
        response = await client.post("/v1/auth/forgot-password", json={"email": "not-an-email"})

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_forgot_password_rejects_unknown_fields(self, client):
        response = await client.post(
            "/v1/auth/forgot-password",
            json={"email": "a@example.com", "organization_id": "x"},
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


class TestForgotPasswordRateLimit:
    """sdd_04 §2.5: POST /auth/forgot-password -- 5 req / IP / hora."""

    async def test_forgot_password_returns_429_after_5_requests_same_ip(self, client):
        for _ in range(5):
            response = await client.post(
                "/v1/auth/forgot-password", json={"email": "cualquiera@example.com"}
            )
            assert response.status_code == 200

        response = await client.post(
            "/v1/auth/forgot-password", json={"email": "cualquiera@example.com"}
        )

        assert response.status_code == 429
        assert response.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
        assert "Retry-After" in response.headers
