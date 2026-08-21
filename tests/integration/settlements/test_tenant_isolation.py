"""tests/integration/settlements/test_tenant_isolation.py -- issue #29.

Invariante: RN-D01 -- los datos de un tenant nunca son accesibles desde
otro tenant. Cubre `POST /settlements/generate` (landlord de otro
tenant) y `GET /settlements/:id` (liquidacion de otro tenant).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _mock_celery_apply_async(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("adminprop.workers.documents_worker.generate_settlement.apply_async", mock)
    return mock


async def _seed_org_with_landlord_property_contract(seed):
    org = await seed.create_organization_with_system_roles()
    landlord_id = await seed.create_landlord_row(organization_id=org["organization_id"])
    renter_id = await seed.create_renter_row(organization_id=org["organization_id"])
    property_id = await seed.create_property_row(
        organization_id=org["organization_id"], landlord_id=landlord_id
    )
    contract_id = await seed.create_contract_row(
        organization_id=org["organization_id"], property_id=property_id, renter_id=renter_id
    )
    return org, landlord_id, contract_id


class TestTenantIsolation:
    """RN-D01 enforcement: tenant A no accede a recursos del tenant B."""

    @pytest.mark.asyncio
    async def test_generate_settlement_for_other_tenant_landlord_returns_404(self, client, seed):
        org_a = await seed.create_organization_with_system_roles()
        owner_a = await seed.add_member(
            organization_id=org_a["organization_id"],
            role_id=org_a["roles"]["owner"],
            role_name="owner",
        )
        _org_b, landlord_b_id, _contract_b_id = await _seed_org_with_landlord_property_contract(
            seed
        )

        response = await client.post(
            "/v1/settlements/generate",
            json={"landlord_id": str(landlord_b_id), "period": "2026-06"},
            headers=owner_a["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_get_settlement_of_other_tenant_returns_404(self, client, seed):
        org_a = await seed.create_organization_with_system_roles()
        owner_a = await seed.add_member(
            organization_id=org_a["organization_id"],
            role_id=org_a["roles"]["owner"],
            role_name="owner",
        )
        org_b, landlord_b_id, contract_b_id = await _seed_org_with_landlord_property_contract(seed)
        owner_b = await seed.add_member(
            organization_id=org_b["organization_id"],
            role_id=org_b["roles"]["owner"],
            role_name="owner",
        )
        await seed.create_rent_period_row(
            organization_id=org_b["organization_id"],
            contract_id=contract_b_id,
            period="2026-06-01",
        )
        generate_response = await client.post(
            "/v1/settlements/generate",
            json={"landlord_id": str(landlord_b_id), "period": "2026-06"},
            headers=owner_b["headers"],
        )
        assert generate_response.status_code == 202
        settlement_b_id = generate_response.json()["data"]["settlement_id"]

        response = await client.get(
            f"/v1/settlements/{settlement_b_id}", headers=owner_a["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_list_settlements_never_returns_other_tenant_rows(self, client, seed):
        org_a = await seed.create_organization_with_system_roles()
        owner_a = await seed.add_member(
            organization_id=org_a["organization_id"],
            role_id=org_a["roles"]["owner"],
            role_name="owner",
        )
        org_b, landlord_b_id, contract_b_id = await _seed_org_with_landlord_property_contract(seed)
        owner_b = await seed.add_member(
            organization_id=org_b["organization_id"],
            role_id=org_b["roles"]["owner"],
            role_name="owner",
        )
        await seed.create_rent_period_row(
            organization_id=org_b["organization_id"],
            contract_id=contract_b_id,
            period="2026-06-01",
        )
        generate_response = await client.post(
            "/v1/settlements/generate",
            json={"landlord_id": str(landlord_b_id), "period": "2026-06"},
            headers=owner_b["headers"],
        )
        settlement_b_id = generate_response.json()["data"]["settlement_id"]

        response = await client.get("/v1/settlements", headers=owner_a["headers"])

        assert response.status_code == 200
        ids = {row["id"] for row in response.json()["data"]}
        assert settlement_b_id not in ids
