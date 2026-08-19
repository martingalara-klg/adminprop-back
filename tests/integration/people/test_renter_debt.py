"""tests/integration/people/test_renter_debt.py -- issue #23.

SDD: spec_module_02_personas.md CA-02-05 + sdd_03 §6
`GET /renters/:id/debt`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest

pytestmark = pytest.mark.asyncio


def _previous_month(today: date) -> date:
    if today.month == 1:
        return date(today.year - 1, 12, 1)
    return date(today.year, today.month - 1, 1)


async def _seed_contract(seed):
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
    return org, owner, renter_id, property_id, contract_id


class TestRenterDebt:
    """CA-02-05: la ficha del inquilino muestra sus contratos y su estado
    de deuda con: periodos adeudados, saldo, dias de mora e interes
    sugerido acumulado."""

    async def test_ca_02_05_renter_debt_shows_overdue_periods_balance_and_interest(
        self, client, seed
    ):
        org, owner, renter_id, property_id, contract_id = await _seed_contract(seed)
        today = datetime.now(tz=UTC).date()
        await seed.create_rent_period_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            period=_previous_month(today).isoformat(),
            amount_due="1000.00",
            paid_total="300.00",
            status="partial",
        )

        response = await client.get(f"/v1/renters/{renter_id}/debt", headers=owner["headers"])

        assert response.status_code == 200
        entries = response.json()["data"]
        assert len(entries) == 1
        entry = entries[0]
        assert entry["contract_id"] == str(contract_id)
        assert entry["property_id"] == str(property_id)
        assert entry["renter_id"] == str(renter_id)
        assert entry["periods_overdue"] == 1
        assert entry["balance"] == "700.00"
        assert entry["days_late"] > 0
        assert float(entry["suggested_interest"]) > 0

    async def test_renter_without_debt_returns_empty_list(self, client, seed):
        org, owner, renter_id, _, contract_id = await _seed_contract(seed)
        await seed.create_rent_period_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            amount_due="1000.00",
            paid_total="1000.00",
            status="paid",
        )

        response = await client.get(f"/v1/renters/{renter_id}/debt", headers=owner["headers"])

        assert response.status_code == 200
        assert response.json()["data"] == []

    async def test_renter_debt_for_nonexistent_renter_returns_404(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )

        response = await client.get(f"/v1/renters/{uuid.uuid4()}/debt", headers=owner["headers"])

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_renter_debt_of_another_organization_returns_404(self, client, seed):
        """RN-D01: aislamiento cross-tenant -- 404, no 403."""
        org_a = await seed.create_organization_with_system_roles(name="Org A")
        owner_a = await seed.add_member(
            organization_id=org_a["organization_id"],
            role_id=org_a["roles"]["owner"],
            role_name="owner",
        )
        org_b = await seed.create_organization_with_system_roles(name="Org B")
        renter_b_id = await seed.create_renter_row(organization_id=org_b["organization_id"])

        response = await client.get(f"/v1/renters/{renter_b_id}/debt", headers=owner_a["headers"])

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_maintenance_role_cannot_view_renter_debt(self, client, seed):
        """RN-A01: `maintenance` no tiene `renter:read`."""
        org = await seed.create_organization_with_system_roles()
        maintenance = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["maintenance"],
            role_name="maintenance",
        )
        renter_id = await seed.create_renter_row(organization_id=org["organization_id"])

        response = await client.get(f"/v1/renters/{renter_id}/debt", headers=maintenance["headers"])

        assert response.status_code == 403
