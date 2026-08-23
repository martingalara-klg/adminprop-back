"""Issue #4 — cliente de email Resend (docs/skills/external-integrations.md).

CA-4-03: el cliente se testea contra fixtures JSON deterministas, nunca
contra el servicio real de Resend (docs/skills/external-integrations.md
checklist: "El cliente de Resend se mockea con fixtures JSON en tests...
nunca se llama al servicio real desde CI").

SDD: infrastructure/spec_notificaciones.md §"Email".
"""

import json
from pathlib import Path

import httpx
import pytest

from adminprop.shared.email.sender import send_email
from adminprop.shared.errors.retryable import (
    NonRetryableNotificationError,
    RetryableNotificationError,
)

pytestmark = pytest.mark.asyncio

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "external"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _fake_post(status_code: int, fixture_name: str):
    body = _load_fixture(fixture_name)

    async def _post(self, url, *, json=None, headers=None):
        return httpx.Response(status_code, json=body, request=httpx.Request("POST", url))

    return _post


async def test_ca_4_03_send_email_returns_message_id_from_resend_fixture(monkeypatch):
    """CA-4-03: un envio exitoso usa el fixture `resend_send_ok.json`, no Resend real."""
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post(200, "resend_send_ok.json"))

    message_id = await send_email(
        to=["owner@example.com"],
        subject="Bienvenido",
        html="<p>hola</p>",
        text=None,
        organization_name="Acme SRL",
        owner_reply_email=None,
        request_id="req-1",
    )

    assert message_id == "4ef9a417-02e9-4d39-ad75-9611e0fcc33c"


async def test_ca_4_02_send_email_raises_retryable_on_429_fixture(monkeypatch):
    """CA-4-02: 429 (rate limit) del proveedor clasifica como Retryable."""
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post(429, "resend_send_error_429.json"))

    with pytest.raises(RetryableNotificationError):
        await send_email(
            to=["x@y.com"],
            subject="s",
            html="h",
            text=None,
            organization_name="Acme",
            owner_reply_email=None,
            request_id="req-2",
        )


@pytest.mark.parametrize("status_code", [500, 502, 503, 504])
async def test_ca_4_02_send_email_raises_retryable_on_5xx(monkeypatch, status_code):
    """CA-4-02: 5xx del proveedor clasifica como Retryable."""
    monkeypatch.setattr(
        httpx.AsyncClient, "post", _fake_post(status_code, "resend_send_error_429.json")
    )

    with pytest.raises(RetryableNotificationError):
        await send_email(
            to=["x@y.com"],
            subject="s",
            html="h",
            text=None,
            organization_name="Acme",
            owner_reply_email=None,
            request_id="req-3",
        )


async def test_ca_4_02_send_email_raises_non_retryable_on_400_fixture(monkeypatch):
    """CA-4-02: 400 (email invalido) del proveedor clasifica como NonRetryable."""
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post(400, "resend_send_error_400.json"))

    with pytest.raises(NonRetryableNotificationError):
        await send_email(
            to=["not-an-email"],
            subject="s",
            html="h",
            text=None,
            organization_name="Acme",
            owner_reply_email=None,
            request_id="req-4",
        )


async def test_fa_send_email_raises_retryable_on_timeout(monkeypatch):
    """FA: timeout de red hacia Resend clasifica como Retryable (sdd_04 §1.3)."""

    async def _raise_timeout(self, url, *, json=None, headers=None):
        raise httpx.TimeoutException("connect timeout")

    monkeypatch.setattr(httpx.AsyncClient, "post", _raise_timeout)

    with pytest.raises(RetryableNotificationError):
        await send_email(
            to=["x@y.com"],
            subject="s",
            html="h",
            text=None,
            organization_name="Acme",
            owner_reply_email=None,
            request_id="req-5",
        )


async def test_send_email_sets_dynamic_from_header_reply_to_and_request_id(monkeypatch):
    """spec_notificaciones.md §Email: From dinamico con el nombre de la
    organizacion, Reply-To al owner, X-Request-Id propagado como header."""
    captured: dict = {}

    async def _post(self, url, *, json=None, headers=None):
        captured["json"] = json
        captured["headers"] = headers
        return httpx.Response(200, json={"id": "abc"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)

    await send_email(
        to=["x@y.com"],
        subject="s",
        html="h",
        text=None,
        organization_name="Acme SRL",
        owner_reply_email="owner@acme.com",
        request_id="req-9",
    )

    assert captured["json"]["from"] == "AdminProp · Acme SRL <noreply@adminprop.local>"
    assert captured["json"]["reply_to"] == "owner@acme.com"
    assert captured["json"]["headers"]["X-Request-Id"] == "req-9"


async def test_send_email_omits_reply_to_when_owner_email_unknown(monkeypatch):
    """Si no se conoce el owner (aun no implementado en este issue), no se
    envia un `reply_to` invalido/vacio a Resend."""
    captured: dict = {}

    async def _post(self, url, *, json=None, headers=None):
        captured["json"] = json
        return httpx.Response(200, json={"id": "abc"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)

    await send_email(
        to=["x@y.com"],
        subject="s",
        html="h",
        text=None,
        organization_name="Acme",
        owner_reply_email=None,
        request_id="req-10",
    )

    assert "reply_to" not in captured["json"]
