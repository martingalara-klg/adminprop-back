"""Issue #1 — CA: GET /health responde con checks de DB y Redis."""

from fastapi.testclient import TestClient


def test_ca_health_ok_cuando_db_y_redis_responden(client: TestClient, monkeypatch):
    """CA #1-02: /health responde con checks de DB y Redis (ambos ok -> 200)."""

    async def fake_check(url: str, timeout: float) -> str:
        return "ok"

    monkeypatch.setattr("adminprop.modules.health.router.service.check_tcp", fake_check)
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["status"] == "ok"
    assert body["data"]["checks"] == {"database": "ok", "redis": "ok"}


def test_ca_health_degraded_cuando_una_dependencia_cae(client: TestClient, monkeypatch):
    """CA #1-02: dependencia caida -> 503 degraded, sin romper la app."""

    async def fake_check(url: str, timeout: float) -> str:
        return "unreachable" if "5432" in url or url.startswith("postgresql") else "ok"

    monkeypatch.setattr("adminprop.modules.health.router.service.check_tcp", fake_check)
    response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["data"]["status"] == "degraded"
    assert response.json()["data"]["checks"]["database"] == "unreachable"


def test_check_tcp_unreachable_contra_puerto_cerrado():
    """El check real devuelve unreachable rapido ante un puerto sin servicio."""
    import asyncio

    from adminprop.modules.health.service import check_tcp

    result = asyncio.run(check_tcp("postgresql://localhost:59999/x", timeout=0.2))
    assert result == "unreachable"
