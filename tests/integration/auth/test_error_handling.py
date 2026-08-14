"""Test del handler catch-all de excepciones no controladas (issue #6).

SDD: docs/skills/error-handling.md -- "el catch-all Exception retorna
INTERNAL_ERROR generico (no expone str(exc))".
"""

import pytest

from adminprop.main import create_app
from adminprop.modules.auth.service import get_auth_service

pytestmark = pytest.mark.asyncio


class _ExplodingService:
    async def login(self, **kwargs):
        raise RuntimeError("boom -- nunca deberia llegar al cliente")


async def test_unhandled_exception_returns_500_internal_error_without_leaking_details(
    rsa_keypair,
):
    from httpx import ASGITransport, AsyncClient

    app = create_app()
    app.dependency_overrides[get_auth_service] = lambda: _ExplodingService()

    # raise_app_exceptions=False: Starlette re-lanza la excepcion original
    # ademas de enviar la respuesta ya formada por el exception handler (para
    # que otras capas -- Sentry, etc -- la observen); sin este flag httpx
    # la propagarira como error del cliente en vez de exponer la response 500
    # real que el servidor ya envio.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://testserver") as client:
        response = await client.post(
            "/v1/auth/login", json={"email": "a@example.com", "password": "x"}
        )

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert "boom" not in body["error"]["message"]
    app.dependency_overrides.clear()
