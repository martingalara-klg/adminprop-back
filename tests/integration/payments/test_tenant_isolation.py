"""tests/integration/payments/test_tenant_isolation.py -- issue #22.

Invariante: RN-D01 -- los datos de un tenant nunca son accesibles desde
otro tenant. Cubre `GET .../interest-preview` y `POST .../payments`.
"""

from __future__ import annotations

import pytest


async def _seed_org_with_rent_period(seed, *, amount_due: str = "1000.00"):
    org = await seed.create_organization_with_system_roles()
    landlord_id = await seed.create_landlord_row(organization_id=org["organization_id"])
    renter_id = await seed.create_renter_row(organization_id=org["organization_id"])
    property_id = await seed.create_property_row(
        organization_id=org["organization_id"], landlord_id=landlord_id
    )
    contract_id = await seed.create_contract_row(
        organization_id=org["organization_id"], property_id=property_id, renter_id=renter_id
    )
    rent_period_id = await seed.create_rent_period_row(
        organization_id=org["organization_id"], contract_id=contract_id, amount_due=amount_due
    )
    return org, rent_period_id


class TestTenantIsolation:
    """RN-D01 enforcement: tenant A no accede a recursos del tenant B."""

    @pytest.mark.asyncio
    async def test_preview_interest_for_other_tenant_rent_period_returns_404(self, client, seed):
        org_a = await seed.create_organization_with_system_roles()
        owner_a = await seed.add_member(
            organization_id=org_a["organization_id"],
            role_id=org_a["roles"]["owner"],
            role_name="owner",
        )
        _, rent_period_b_id = await _seed_org_with_rent_period(seed)

        response = await client.get(
            f"/v1/rent-periods/{rent_period_b_id}/interest-preview",
            params={"payment_date": "2026-06-15"},
            headers=owner_a["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_register_payment_for_other_tenant_rent_period_returns_404(self, client, seed):
        org_a = await seed.create_organization_with_system_roles()
        owner_a = await seed.add_member(
            organization_id=org_a["organization_id"],
            role_id=org_a["roles"]["owner"],
            role_name="owner",
        )
        _, rent_period_b_id = await _seed_org_with_rent_period(seed)

        response = await client.post(
            f"/v1/rent-periods/{rent_period_b_id}/payments",
            json={
                "payment_date": "2026-06-05",
                "method": "cash",
                "payment_currency": "ARS",
                "amount": "100.00",
                "destination": "agency_account",
                "charged_interest": "0.00",
            },
            headers=owner_a["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

        # El cobro NO se registro contra el periodo de la organizacion B.
        rent_period_b = await seed.get_rent_period(rent_period_b_id)
        assert rent_period_b["status"] == "pending"
        assert rent_period_b["paid_total"] == "0.00"
