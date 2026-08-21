"""tests/integration/settlements/test_regenerate_settlement.py -- issue #30.

SDD: docs/sdd/features/spec_module_05_liquidaciones.md §RF-03 +
core/sdd_03_api_contracts.md §11 "POST /settlements/:id/regenerate".

El recalculo real (CA-05-05, CA-05-06) se prueba contra Postgres real en
tests/integration/workers/test_documents_worker_regenerate.py -- estos
tests HTTP mockean `regenerate_settlement.apply_async` (mismo criterio
que `test_generate_settlement.py` para #29).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from adminprop.modules.settlements.job_status import set_job_status

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _mock_celery_apply_async(monkeypatch):
    mock_generate = MagicMock()
    mock_regenerate = MagicMock()
    monkeypatch.setattr(
        "adminprop.workers.documents_worker.generate_settlement.apply_async", mock_generate
    )
    monkeypatch.setattr(
        "adminprop.workers.documents_worker.regenerate_settlement.apply_async", mock_regenerate
    )
    return mock_regenerate


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
    await set_job_status(uuid.UUID(settlement_id), "completed")
    return org, owner, landlord_id, contract_id, settlement_id


class TestRegenerateSettlement:
    """RF-03/RN-L03: regeneracion 202 + auditada."""

    async def test_regenerate_returns_202_and_enqueues_job(
        self, client, seed, _mock_celery_apply_async
    ):
        _org, owner, _landlord_id, _contract_id, settlement_id = await _seed_and_generate(
            client, seed
        )

        response = await client.post(
            f"/v1/settlements/{settlement_id}/regenerate", json={}, headers=owner["headers"]
        )

        assert response.status_code == 202
        data = response.json()["data"]
        assert data["settlement_id"] == settlement_id
        assert data["status"] == "pending"
        _mock_celery_apply_async.assert_called_once()

    async def test_regenerate_issued_settlement_stays_issued(
        self, client, seed, _mock_celery_apply_async
    ):
        """R-04: "una liquidacion emitida sigue siendo regenerable -- la
        flexibilidad es deliberada" -- `status` no cambia al regenerar."""
        _org, owner, _landlord_id, _contract_id, settlement_id = await _seed_and_generate(
            client, seed
        )
        issue_response = await client.post(
            f"/v1/settlements/{settlement_id}/issue", headers=owner["headers"]
        )
        assert issue_response.status_code == 200
        await set_job_status(uuid.UUID(settlement_id), "completed")

        response = await client.post(
            f"/v1/settlements/{settlement_id}/regenerate", json={}, headers=owner["headers"]
        )

        assert response.status_code == 202
        row = await seed.get_settlement_row(settlement_id)
        assert row["status"] == "issued"

    async def test_regenerate_with_new_exchange_rate(self, client, seed, _mock_celery_apply_async):
        _org, owner, _landlord_id, _contract_id, settlement_id = await _seed_and_generate(
            client, seed
        )

        response = await client.post(
            f"/v1/settlements/{settlement_id}/regenerate",
            json={"exchange_rate": "1300.50"},
            headers=owner["headers"],
        )

        assert response.status_code == 202
        _mock_celery_apply_async.assert_called_once()
        _args, kwargs = _mock_celery_apply_async.call_args
        assert "1300.50" in kwargs["args"]

    async def test_regenerate_while_job_in_progress_returns_422(
        self, client, seed, _mock_celery_apply_async
    ):
        _org, owner, _landlord_id, _contract_id, settlement_id = await _seed_and_generate(
            client, seed
        )
        await set_job_status(uuid.UUID(settlement_id), "processing")

        response = await client.post(
            f"/v1/settlements/{settlement_id}/regenerate", json={}, headers=owner["headers"]
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "BUSINESS_RULE_VIOLATION"
        _mock_celery_apply_async.assert_not_called()

    async def test_regenerate_nonexistent_settlement_returns_404(
        self, client, seed, _mock_celery_apply_async
    ):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )

        response = await client.post(
            f"/v1/settlements/{uuid.uuid4()}/regenerate", json={}, headers=owner["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_settlement_read_only_user_cannot_regenerate(
        self, client, seed, auth_headers, _mock_celery_apply_async
    ):
        """Regenerar recalcula y persiste totales -- es una operacion
        mutante de la misma familia que `POST /generate` (RF-03), nunca
        de solo lectura. `settlement:read` (sin `settlement:generate`)
        debe recibir `403 FORBIDDEN`."""
        _org, _owner, _landlord_id, _contract_id, settlement_id = await _seed_and_generate(
            client, seed
        )
        read_only_user = await seed.create_user()
        read_only_headers = auth_headers(
            user_id=read_only_user["id"],
            organization_id=_org["organization_id"],
            role_name="read_only",
            permissions=["settlement:read"],
        )

        response = await client.post(
            f"/v1/settlements/{settlement_id}/regenerate",
            json={},
            headers=read_only_headers,
        )

        assert response.status_code == 403
        _mock_celery_apply_async.assert_not_called()
