"""tests/integration/settlements/test_get_and_list_settlements.py --
issue #29.

SDD: core/sdd_03_api_contracts.md §11 "GET /settlements",
"GET /settlements/:id". Implements: CA-05-03 (job_status/warnings en el
detalle).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _mock_celery_apply_async(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("adminprop.workers.documents_worker.generate_settlement.apply_async", mock)
    return mock


async def _seed_and_generate(client, seed, *, period="2026-06"):
    org = await seed.create_organization_with_system_roles()
    owner = await seed.add_member(
        organization_id=org["organization_id"], role_id=org["roles"]["owner"], role_name="owner"
    )
    landlord_id = await seed.create_landlord_row(organization_id=org["organization_id"])
    renter_id = await seed.create_renter_row(organization_id=org["organization_id"])
    property_id = await seed.create_property_row(
        organization_id=org["organization_id"], landlord_id=landlord_id
    )
    contract_id = await seed.create_contract_row(
        organization_id=org["organization_id"], property_id=property_id, renter_id=renter_id
    )
    await seed.create_rent_period_row(
        organization_id=org["organization_id"], contract_id=contract_id, period=f"{period}-01"
    )

    response = await client.post(
        "/v1/settlements/generate",
        json={"landlord_id": str(landlord_id), "period": period},
        headers=owner["headers"],
    )
    assert response.status_code == 202
    settlement_id = response.json()["data"]["settlement_id"]
    return org, owner, landlord_id, settlement_id


class TestGetSettlementDetail:
    @pytest.mark.asyncio
    async def test_get_settlement_returns_totals_and_line_items_shape(self, client, seed):
        _org, owner, landlord_id, settlement_id = await _seed_and_generate(client, seed)

        response = await client.get(f"/v1/settlements/{settlement_id}", headers=owner["headers"])

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["id"] == settlement_id
        assert data["landlord_id"] == str(landlord_id)
        assert data["status"] == "draft"
        # Job todavia no corrio (Celery mockeado) -- sin clave en Redis
        # todavia, `job_status.py` degrada a "completed" (ver docstring).
        assert data["job_status"] in {"pending", "processing", "completed"}
        assert data["line_items"] == []
        assert data["attachments"] == []

    @pytest.mark.asyncio
    async def test_get_settlement_reflects_with_errors_job_status_from_redis(self, client, seed):
        """CA-05-03: "con periodos impagos o cargos faltantes termina
        with_errors y las advertencias se listan en el detalle"."""
        from adminprop.modules.settlements.job_status import set_job_status

        _org, owner, _landlord_id, settlement_id = await _seed_and_generate(client, seed)
        import uuid

        await set_job_status(
            uuid.UUID(settlement_id), "with_errors", warnings=["Periodo impago en propiedad X."]
        )

        response = await client.get(f"/v1/settlements/{settlement_id}", headers=owner["headers"])

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["job_status"] == "with_errors"
        assert data["warnings"] == ["Periodo impago en propiedad X."]

    @pytest.mark.asyncio
    async def test_get_unknown_settlement_returns_404(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )
        import uuid

        response = await client.get(f"/v1/settlements/{uuid.uuid4()}", headers=owner["headers"])
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"


class TestListSettlements:
    @pytest.mark.asyncio
    async def test_list_filters_by_period_and_landlord_id(self, client, seed):
        _org, owner, landlord_id, settlement_id = await _seed_and_generate(
            client, seed, period="2026-06"
        )

        response = await client.get(
            f"/v1/settlements?period=2026-06&landlord_id={landlord_id}",
            headers=owner["headers"],
        )

        assert response.status_code == 200
        ids = {row["id"] for row in response.json()["data"]}
        assert settlement_id in ids

    @pytest.mark.asyncio
    async def test_list_filters_by_status_draft(self, client, seed):
        _org, owner, _landlord_id, settlement_id = await _seed_and_generate(client, seed)

        response = await client.get("/v1/settlements?status=draft", headers=owner["headers"])

        assert response.status_code == 200
        ids = {row["id"] for row in response.json()["data"]}
        assert settlement_id in ids

        response_issued = await client.get(
            "/v1/settlements?status=issued", headers=owner["headers"]
        )
        assert response_issued.status_code == 200
        assert settlement_id not in {row["id"] for row in response_issued.json()["data"]}
