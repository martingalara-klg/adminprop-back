"""tests/integration/settlements/test_issue_settlement.py -- issue #30.

SDD: docs/sdd/features/spec_module_05_liquidaciones.md §RF-03 +
core/sdd_03_api_contracts.md §11 "POST /settlements/:id/issue".
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from adminprop.modules.settlements.job_status import set_job_status

pytestmark = pytest.mark.asyncio


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
    # Celery mockeado (no corre el worker real) -- simula que el job de
    # calculo ya termino para poder ejercitar `POST .../issue`.
    await set_job_status(uuid.UUID(settlement_id), "completed")
    return org, owner, landlord_id, settlement_id


class TestIssueSettlement:
    """RF-03: `draft -> issued`, unica transicion valida."""

    async def test_issue_draft_settlement_returns_issued_status(self, client, seed):
        _org, owner, _landlord_id, settlement_id = await _seed_and_generate(client, seed)

        response = await client.post(
            f"/v1/settlements/{settlement_id}/issue", headers=owner["headers"]
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "issued"
        assert data["issued_at"] is not None

        row = await seed.get_settlement_row(settlement_id)
        assert row["status"] == "issued"

    async def test_issue_is_audited(self, client, seed):
        org, owner, _landlord_id, settlement_id = await _seed_and_generate(client, seed)

        response = await client.post(
            f"/v1/settlements/{settlement_id}/issue", headers=owner["headers"]
        )
        assert response.status_code == 200

        rows = await seed.audit_rows(org["organization_id"], "settlement.issued")
        assert len(rows) == 1
        assert str(rows[0]["entity_id"]) == str(settlement_id)
        assert str(rows[0]["user_id"]) == str(owner["id"])

    async def test_issuing_an_already_issued_settlement_returns_422(self, client, seed):
        """RF-03: "draft -> issued" es la unica transicion -- una
        liquidacion ya `issued` no vuelve a emitirse (se regenera)."""
        _org, owner, _landlord_id, settlement_id = await _seed_and_generate(client, seed)
        first = await client.post(
            f"/v1/settlements/{settlement_id}/issue", headers=owner["headers"]
        )
        assert first.status_code == 200

        second = await client.post(
            f"/v1/settlements/{settlement_id}/issue", headers=owner["headers"]
        )

        assert second.status_code == 422
        assert second.json()["error"]["code"] == "INVALID_STATUS_TRANSITION"

    async def test_issue_nonexistent_settlement_returns_404(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )

        response = await client.post(
            f"/v1/settlements/{uuid.uuid4()}/issue", headers=owner["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_issue_while_calculation_still_pending_returns_422(self, client, seed):
        """RF-01: mientras el job de calculo (Celery mockeado en este
        test) no termino, `job_status` sigue en `pending` -- no se puede
        emitir una liquidacion cuyos totales todavia no son definitivos."""
        from adminprop.modules.settlements.job_status import set_job_status

        _org, owner, _landlord_id, settlement_id = await _seed_and_generate(client, seed)
        await set_job_status(uuid.UUID(settlement_id), "processing")

        response = await client.post(
            f"/v1/settlements/{settlement_id}/issue", headers=owner["headers"]
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "BUSINESS_RULE_VIOLATION"

    async def test_maintenance_role_cannot_issue_settlement(self, client, seed):
        """RN-A01: `maintenance` no tiene `settlement:issue`."""
        org, _owner, _landlord_id, settlement_id = await _seed_and_generate(client, seed)
        maintenance = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["maintenance"],
            role_name="maintenance",
        )

        response = await client.post(
            f"/v1/settlements/{settlement_id}/issue", headers=maintenance["headers"]
        )

        assert response.status_code == 403
