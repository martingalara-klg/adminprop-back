"""tests/integration/settlements/test_generate_settlement.py -- issue #29.

SDD: docs/sdd/features/spec_module_05_liquidaciones.md §RF-01.
Implements: CA-05-01 (parte HTTP: 202 + placeholder), CA-05-02 (400
SETTLEMENT_EXCHANGE_RATE_REQUIRED), CA-05-03 (202 + status pending), 409
SETTLEMENT_ALREADY_EXISTS, 422 BUSINESS_RULE_VIOLATION.

El calculo real (formula, redondeo, USD, warnings) se prueba en
tests/integration/workers/test_documents_worker.py invocando
`_generate_settlement_async` directamente contra Postgres real -- estos
tests HTTP mockean `generate_settlement.apply_async` (mismo criterio que
tests/unit/shared/test_notifications_service.py.TestEnqueuePendingEmails)
porque no hay un worker Celery corriendo contra Redis en la suite.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


async def _seed_owner_with_property(seed, *, currency: str = "ARS"):
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
        organization_id=org["organization_id"],
        property_id=property_id,
        renter_id=renter_id,
        currency=currency,
    )
    return org, owner, landlord_id, contract_id


@pytest.fixture(autouse=True)
def _mock_celery_apply_async(monkeypatch):
    """Evita depender de un worker Celery real -- mismo criterio que
    `tests/unit/shared/test_notifications_service.py`."""
    mock = MagicMock()
    monkeypatch.setattr("adminprop.workers.documents_worker.generate_settlement.apply_async", mock)
    return mock


class TestGenerateSettlementAccepted:
    """RF-01: `POST /settlements/generate` -> 202 + placeholder `draft`."""

    @pytest.mark.asyncio
    async def test_generate_settlement_returns_202_with_pending_status(
        self, client, seed, _mock_celery_apply_async
    ):
        org, owner, landlord_id, contract_id = await _seed_owner_with_property(seed)
        await seed.create_rent_period_row(
            organization_id=org["organization_id"], contract_id=contract_id, period="2026-06-01"
        )

        response = await client.post(
            "/v1/settlements/generate",
            json={"landlord_id": str(landlord_id), "period": "2026-06"},
            headers=owner["headers"],
        )

        assert response.status_code == 202
        data = response.json()["data"]
        assert data["status"] == "pending"
        assert data["settlement_id"]
        assert data["estimated_completion_seconds"] > 0
        _mock_celery_apply_async.assert_called_once()

        row = await seed.get_settlement_row(data["settlement_id"])
        assert row["status"] == "draft"
        assert str(row["commission_pct_used"]) == "10.0000"

    @pytest.mark.asyncio
    async def test_generate_settlement_for_unknown_landlord_returns_404(
        self, client, seed, _mock_celery_apply_async
    ):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )
        import uuid

        response = await client.post(
            "/v1/settlements/generate",
            json={"landlord_id": str(uuid.uuid4()), "period": "2026-06"},
            headers=owner["headers"],
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"


class TestCa0502ExchangeRateRequired:
    """CA-05-02: "Con una propiedad USD en el período y sin TC, POST
    /settlements/generate devuelve 400 SETTLEMENT_EXCHANGE_RATE_REQUIRED"."""

    @pytest.mark.asyncio
    async def test_usd_payment_without_exchange_rate_returns_400(
        self, client, seed, _mock_celery_apply_async
    ):
        # Issue #72/RN-L06: el gate decide por la moneda del CONTRATO.
        org, owner, landlord_id, contract_id = await _seed_owner_with_property(seed, currency="USD")
        rent_period_id = await seed.create_rent_period_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            period="2026-06-01",
            currency="USD",
        )
        await seed.create_payment_row(
            organization_id=org["organization_id"],
            rent_period_id=rent_period_id,
            created_by=owner["id"],
            payment_currency="USD",
            amount="500.00",
        )

        response = await client.post(
            "/v1/settlements/generate",
            json={"landlord_id": str(landlord_id), "period": "2026-06"},
            headers=owner["headers"],
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "SETTLEMENT_EXCHANGE_RATE_REQUIRED"
        _mock_celery_apply_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_usd_contract_paid_in_ars_without_exchange_rate_returns_400(
        self, client, seed, _mock_celery_apply_async
    ):
        """Issue #72 (RN-L06): un contrato USD cobrado en pesos
        (`payment_currency=ARS`) sigue siendo un "cobro USD" para el gate
        -- `payments.amount` esta en la moneda del CONTRATO (RN-P06), no
        en la moneda en la que se cobro fisicamente."""
        org, owner, landlord_id, contract_id = await _seed_owner_with_property(seed, currency="USD")
        rent_period_id = await seed.create_rent_period_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            period="2026-06-01",
            currency="USD",
        )
        await seed.create_payment_row(
            organization_id=org["organization_id"],
            rent_period_id=rent_period_id,
            created_by=owner["id"],
            payment_currency="ARS",
            amount="500.00",
        )

        response = await client.post(
            "/v1/settlements/generate",
            json={"landlord_id": str(landlord_id), "period": "2026-06"},
            headers=owner["headers"],
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "SETTLEMENT_EXCHANGE_RATE_REQUIRED"
        _mock_celery_apply_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_usd_payment_with_exchange_rate_returns_202(
        self, client, seed, _mock_celery_apply_async
    ):
        # Issue #72/RN-L06: el gate decide por la moneda del CONTRATO.
        org, owner, landlord_id, contract_id = await _seed_owner_with_property(seed, currency="USD")
        rent_period_id = await seed.create_rent_period_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            period="2026-06-01",
            currency="USD",
        )
        await seed.create_payment_row(
            organization_id=org["organization_id"],
            rent_period_id=rent_period_id,
            created_by=owner["id"],
            payment_currency="USD",
            amount="500.00",
        )

        response = await client.post(
            "/v1/settlements/generate",
            json={
                "landlord_id": str(landlord_id),
                "period": "2026-06",
                "exchange_rate": "1000.00",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 202


class TestSettlementAlreadyExists:
    @pytest.mark.asyncio
    async def test_second_generate_for_same_landlord_and_period_returns_409(
        self, client, seed, _mock_celery_apply_async
    ):
        org, owner, landlord_id, contract_id = await _seed_owner_with_property(seed)
        await seed.create_rent_period_row(
            organization_id=org["organization_id"], contract_id=contract_id, period="2026-06-01"
        )

        first = await client.post(
            "/v1/settlements/generate",
            json={"landlord_id": str(landlord_id), "period": "2026-06"},
            headers=owner["headers"],
        )
        assert first.status_code == 202

        second = await client.post(
            "/v1/settlements/generate",
            json={"landlord_id": str(landlord_id), "period": "2026-06"},
            headers=owner["headers"],
        )
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "SETTLEMENT_ALREADY_EXISTS"


class TestBusinessRuleViolation:
    """RF-02 §Validaciones: "no se puede generar la liquidación de un
    período si el propietario no tiene ninguna propiedad con contrato
    activo ni movimientos en ese mes"."""

    @pytest.mark.asyncio
    async def test_landlord_without_active_contract_or_movements_returns_422(
        self, client, seed, _mock_celery_apply_async
    ):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )
        landlord_id = await seed.create_landlord_row(organization_id=org["organization_id"])

        response = await client.post(
            "/v1/settlements/generate",
            json={"landlord_id": str(landlord_id), "period": "2026-06"},
            headers=owner["headers"],
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "BUSINESS_RULE_VIOLATION"
