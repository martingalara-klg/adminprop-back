"""tests/integration/people/test_landlord_settlements.py -- issue #30.

SDD: core/sdd_03_api_contracts.md §5 "GET /landlords/:id/settlements"
(historial de liquidaciones) + spec_module_05_liquidaciones.md §CA-05-07
("descargables desde el detalle y desde la ficha del propietario").
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _mock_celery_apply_async(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("adminprop.workers.documents_worker.generate_settlement.apply_async", mock)
    return mock


class TestLandlordSettlementsHistory:
    """CA-05-07: la ficha del propietario lista sus liquidaciones."""

    async def test_lists_settlements_for_the_landlord(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
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
            organization_id=org["organization_id"], contract_id=contract_id, period="2026-06-01"
        )

        generate_response = await client.post(
            "/v1/settlements/generate",
            json={"landlord_id": str(landlord_id), "period": "2026-06"},
            headers=owner["headers"],
        )
        assert generate_response.status_code == 202
        settlement_id = generate_response.json()["data"]["settlement_id"]

        response = await client.get(
            f"/v1/landlords/{landlord_id}/settlements", headers=owner["headers"]
        )

        assert response.status_code == 200
        ids = {row["id"] for row in response.json()["data"]}
        assert settlement_id in ids

    async def test_landlord_without_settlements_returns_empty_list(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )
        landlord_id = await seed.create_landlord_row(organization_id=org["organization_id"])

        response = await client.get(
            f"/v1/landlords/{landlord_id}/settlements", headers=owner["headers"]
        )

        assert response.status_code == 200
        assert response.json()["data"] == []

    async def test_unknown_landlord_returns_404(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )

        response = await client.get(
            f"/v1/landlords/{uuid.uuid4()}/settlements", headers=owner["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_landlord_of_other_tenant_returns_404(self, client, seed):
        org_a = await seed.create_organization_with_system_roles()
        owner_a = await seed.add_member(
            organization_id=org_a["organization_id"],
            role_id=org_a["roles"]["owner"],
            role_name="owner",
        )
        org_b = await seed.create_organization_with_system_roles()
        landlord_b_id = await seed.create_landlord_row(organization_id=org_b["organization_id"])

        response = await client.get(
            f"/v1/landlords/{landlord_b_id}/settlements", headers=owner_a["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
