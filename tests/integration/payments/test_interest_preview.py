"""tests/integration/payments/test_interest_preview.py -- issue #22.

SDD: spec_module_04_cobranzas.md §RF-04 + sdd_03 §9
`GET /rent-periods/:id/interest-preview`.
"""

from __future__ import annotations

import uuid

import pytest


class TestInterestPreview:
    """RF-04 -- interes sugerido a `payment_date` (RN-P02/P03)."""

    @pytest.mark.asyncio
    async def test_ca_04_05_previews_5_days_late_interest_with_grace_day_10(self, client, seed):
        """CA-04-05: "pagando el dia 15 con dia de gracia 10, el sistema
        sugiere interes por 5 dias de mora con el % del contrato"."""
        org = await seed.create_organization_with_system_roles(grace_day=10)
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
            organization_id=org["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            currency="ARS",
            daily_late_fee_pct="1.0",
        )
        rent_period_id = await seed.create_rent_period_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            period="2026-06-01",
            amount_due="1000.00",
        )

        response = await client.get(
            f"/v1/rent-periods/{rent_period_id}/interest-preview",
            params={"payment_date": "2026-06-15"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["days_late"] == 5
        assert data["balance"] == "1000.00"
        assert data["suggested_interest"] == "50.00"

    @pytest.mark.asyncio
    async def test_previews_zero_interest_within_grace_day(self, client, seed):
        """RN-P02: "en termino hasta el dia de gracia inclusive"."""
        org = await seed.create_organization_with_system_roles(grace_day=10)
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
            organization_id=org["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            daily_late_fee_pct="1.0",
        )
        rent_period_id = await seed.create_rent_period_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            period="2026-06-01",
            amount_due="1000.00",
        )

        response = await client.get(
            f"/v1/rent-periods/{rent_period_id}/interest-preview",
            params={"payment_date": "2026-06-10"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["days_late"] == 0
        assert data["suggested_interest"] == "0.00"

    @pytest.mark.asyncio
    async def test_previews_interest_only_over_remaining_balance_after_partial_payment(
        self, client, seed
    ):
        """CA-04-04: "el interes de un pago posterior se calcula solo
        sobre el saldo restante"."""
        org = await seed.create_organization_with_system_roles(grace_day=10)
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
            organization_id=org["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            daily_late_fee_pct="1.0",
        )
        rent_period_id = await seed.create_rent_period_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            period="2026-06-01",
            amount_due="1000.00",
            status="partial",
            paid_total="400.00",
        )

        response = await client.get(
            f"/v1/rent-periods/{rent_period_id}/interest-preview",
            params={"payment_date": "2026-06-15"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["balance"] == "600.00"
        assert data["suggested_interest"] == "30.00"

    @pytest.mark.asyncio
    async def test_unknown_rent_period_returns_404(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )

        response = await client.get(
            f"/v1/rent-periods/{uuid.uuid4()}/interest-preview",
            params={"payment_date": "2026-06-15"},
            headers=owner["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_maintenance_role_cannot_preview_interest(self, client, seed):
        """RN-A01: `maintenance` no tiene `rent-period:read`."""
        org = await seed.create_organization_with_system_roles()
        maintenance = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["maintenance"],
            role_name="maintenance",
        )

        response = await client.get(
            f"/v1/rent-periods/{uuid.uuid4()}/interest-preview",
            params={"payment_date": "2026-06-15"},
            headers=maintenance["headers"],
        )

        assert response.status_code == 403
