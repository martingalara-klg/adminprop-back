"""tests/integration/payments/test_debt.py -- issue #23.

SDD: spec_module_04_cobranzas.md §RF-06 + sdd_03 §9
`GET /debt?landlord_id=&renter_id=&min_days=`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

pytestmark = pytest.mark.asyncio


def _months_ago(today: date, months: int) -> date:
    year = today.year
    month = today.month - months
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


async def _seed_contract(seed, *, daily_late_fee_pct: str = "1.0"):
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
        organization_id=org["organization_id"],
        property_id=property_id,
        renter_id=renter_id,
        daily_late_fee_pct=daily_late_fee_pct,
    )
    return org, owner, landlord_id, renter_id, property_id, contract_id


class TestDebtGlobal:
    """CA-04-09: el estado de deuda global muestra por inquilino/propiedad
    los periodos adeudados con saldo, dias de mora e interes sugerido
    acumulado; filtrable por `min_days`."""

    async def test_ca_04_09_debt_entry_aggregates_balance_and_interest_across_periods(
        self, client, seed
    ):
        """CA-04-09: "saldo, dias de mora e interes sugerido acumulado" --
        dos periodos impagos del mismo contrato se suman en una sola fila."""
        org, owner, landlord_id, renter_id, property_id, contract_id = await _seed_contract(seed)
        today = datetime.now(tz=UTC).date()
        await seed.create_rent_period_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            period=_months_ago(today, 2).isoformat(),
            amount_due="1000.00",
            paid_total="0.00",
            status="pending",
        )
        await seed.create_rent_period_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            period=_months_ago(today, 1).isoformat(),
            amount_due="1000.00",
            paid_total="200.00",
            status="partial",
        )

        response = await client.get("/v1/debt", headers=owner["headers"])

        assert response.status_code == 200
        entries = {item["contract_id"]: item for item in response.json()["data"]}
        entry = entries[str(contract_id)]
        assert entry["property_id"] == str(property_id)
        assert entry["landlord_id"] == str(landlord_id)
        assert entry["renter_id"] == str(renter_id)
        assert entry["periods_overdue"] == 2
        assert entry["balance"] == "1800.00"
        assert entry["days_late"] > 0
        assert float(entry["suggested_interest"]) > 0

    async def test_paid_periods_are_excluded_from_debt(self, client, seed):
        org, owner, _, _, _, contract_id = await _seed_contract(seed)
        await seed.create_rent_period_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            period="2026-01-01",
            amount_due="1000.00",
            paid_total="1000.00",
            status="paid",
        )

        response = await client.get("/v1/debt", headers=owner["headers"])

        assert response.status_code == 200
        assert response.json()["data"] == []

    async def test_filter_by_min_days_excludes_recent_debt(self, client, seed):
        org, owner, _, _, _, contract_id = await _seed_contract(seed)
        today = datetime.now(tz=UTC).date()
        old_id = await seed.create_rent_period_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            period=_months_ago(today, 3).isoformat(),
            status="pending",
        )

        response = await client.get(
            "/v1/debt", params={"min_days": 10000}, headers=owner["headers"]
        )

        assert response.status_code == 200
        assert response.json()["data"] == []

        response_low = await client.get(
            "/v1/debt", params={"min_days": 1}, headers=owner["headers"]
        )
        assert response_low.status_code == 200
        contract_ids = {item["contract_id"] for item in response_low.json()["data"]}
        # El contrato del periodo viejo debe seguir apareciendo con un
        # umbral bajo.
        assert old_id  # el periodo referenciado existe
        assert str(contract_id) in contract_ids

    async def test_filter_by_landlord_id_and_renter_id(self, client, seed):
        org, owner, landlord_id, renter_id, _, contract_id = await _seed_contract(seed)
        await seed.create_rent_period_row(
            organization_id=org["organization_id"], contract_id=contract_id, status="pending"
        )
        other_landlord_id = await seed.create_landlord_row(organization_id=org["organization_id"])
        other_renter_id = await seed.create_renter_row(organization_id=org["organization_id"])
        other_property_id = await seed.create_property_row(
            organization_id=org["organization_id"], landlord_id=other_landlord_id
        )
        other_contract_id = await seed.create_contract_row(
            organization_id=org["organization_id"],
            property_id=other_property_id,
            renter_id=other_renter_id,
        )
        await seed.create_rent_period_row(
            organization_id=org["organization_id"], contract_id=other_contract_id, status="pending"
        )

        by_landlord = await client.get(
            "/v1/debt", params={"landlord_id": str(landlord_id)}, headers=owner["headers"]
        )
        by_renter = await client.get(
            "/v1/debt", params={"renter_id": str(renter_id)}, headers=owner["headers"]
        )

        for response in (by_landlord, by_renter):
            assert response.status_code == 200
            contract_ids = {item["contract_id"] for item in response.json()["data"]}
            assert contract_ids == {str(contract_id)}

    async def test_maintenance_role_cannot_view_global_debt(self, client, seed):
        """RN-A01: `maintenance` no tiene `rent-period:read`."""
        org = await seed.create_organization_with_system_roles()
        maintenance = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["maintenance"],
            role_name="maintenance",
        )

        response = await client.get("/v1/debt", headers=maintenance["headers"])

        assert response.status_code == 403
