"""Test de rate limit en POST /v1/auth/login (issue #6).

SDD: core/sdd_04_nonfunctional.md parrafo 2.5 -- "POST /auth/login: 10
req / IP / 10 min -> 429 + Retry-After".
"""

import pytest

pytestmark = pytest.mark.asyncio


class TestLoginRateLimit:
    async def test_login_returns_429_after_10_requests_same_ip(self, client, seed):
        member = await seed.create_active_member_with_org()

        for _ in range(10):
            response = await client.post(
                "/v1/auth/login", json={"email": member["email"], "password": member["password"]}
            )
            assert response.status_code == 200

        response = await client.post(
            "/v1/auth/login", json={"email": member["email"], "password": member["password"]}
        )

        assert response.status_code == 429
        body = response.json()
        assert body["error"]["code"] == "RATE_LIMIT_EXCEEDED"
        assert "Retry-After" in response.headers
        assert int(response.headers["Retry-After"]) > 0
