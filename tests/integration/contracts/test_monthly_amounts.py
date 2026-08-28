"""tests/integration/contracts/test_monthly_amounts.py

SDD: docs/sdd/features/spec_module_03_contratos.md RF-06 (RN-09, issue #106) +
core/sdd_03_api_contracts.md §8 v1.12 "GET /contracts/:id".
Implements: CA-03-16, CA-03-17, CA-03-18, CA-03-19, CA-03-20, CA-03-21,
            CA-03-22.

Ejercita `GET /v1/contracts/:id` end-to-end: la resolucion de datos
(ajustes `applied` via `ContractAdjustmentRepository.list_applied_by_contract`,
`terminated_at` via `ContractRepository.get_terminated_at` contra
`audit_logs`) + el calculo puro (`monthly_amounts.compute_monthly_amounts`,
cubierto exhaustivamente en `tests/unit/modules/contracts/test_monthly_amounts.py`).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

pytestmark = pytest.mark.asyncio


async def _seed_org_with_owner(seed):
    org = await seed.create_organization_with_system_roles()
    owner = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["owner"],
        role_name="owner",
    )
    return org, owner


async def _seed_property_and_renter(seed, organization_id):
    landlord_id = await seed.create_landlord_row(organization_id=organization_id)
    property_id = await seed.create_property_row(
        organization_id=organization_id, landlord_id=landlord_id
    )
    renter_id = await seed.create_renter_row(organization_id=organization_id)
    return property_id, renter_id


class TestCA0316GetContractFlatSeriesWithoutAdjustments:
    """CA-03-16: contrato sin ajustes -- `monthly_amounts[]` con
    `initial_amount` en todos los meses desde `start_date` hasta el mes
    actual, orden descendente."""

    async def test_ca_03_16_no_adjustments_returns_flat_series_descending(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        today = datetime.now(UTC).date()
        start_date = date(today.year, today.month, 1) - timedelta(days=90)
        contract_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            currency="ARS",
            initial_amount="100000.00",
            start_date=start_date.isoformat(),
            end_date=(today + timedelta(days=365)).isoformat(),
            status="active",
        )

        response = await client.get(f"/v1/contracts/{contract_id}", headers=owner["headers"])

        assert response.status_code == 200
        rows = response.json()["data"]["monthly_amounts"]
        assert len(rows) >= 1
        assert rows[0]["period"] == date(today.year, today.month, 1).isoformat()
        assert all(row["amount"] == "100000.00" for row in rows)


class TestCA0317GetContractWithTwoAppliedAdjustments:
    """CA-03-17: contrato con 2 ajustes `applied` -- 3 tramos de monto en
    `monthly_amounts[]`."""

    async def test_ca_03_17_two_applied_adjustments_produce_three_segments(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        contract_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            currency="ARS",
            initial_amount="100000.00",
            start_date="2026-01-01",
            end_date="2027-01-01",
            status="active",
        )
        await seed.create_adjustment_row(
            organization_id=owner["organization_id"],
            contract_id=contract_id,
            due_period="2026-03-01",
            status="applied",
            previous_amount="100000.00",
            pct_applied="20.0",
            new_amount="120000.00",
        )
        await seed.create_adjustment_row(
            organization_id=owner["organization_id"],
            contract_id=contract_id,
            due_period="2026-05-01",
            status="applied",
            previous_amount="120000.00",
            pct_applied="16.6667",
            new_amount="140000.00",
        )
        # Un ajuste `pending` NO debe afectar el calculo (solo `applied` cuenta).
        await seed.create_adjustment_row(
            organization_id=owner["organization_id"],
            contract_id=contract_id,
            due_period="2026-07-01",
            status="pending",
            previous_amount="140000.00",
        )

        response = await client.get(f"/v1/contracts/{contract_id}", headers=owner["headers"])

        assert response.status_code == 200
        rows = response.json()["data"]["monthly_amounts"]
        by_period = {row["period"]: row["amount"] for row in rows}
        assert by_period["2026-01-01"] == "100000.00"
        assert by_period["2026-02-01"] == "100000.00"
        assert by_period["2026-03-01"] == "120000.00"
        assert by_period["2026-04-01"] == "120000.00"
        assert by_period["2026-05-01"] == "140000.00"


class TestCA0318GetContractWithRetroactiveInitialLoad:
    """CA-03-18: contrato con carga inicial retroactiva (issue #100) --
    `monthly_amounts[]` refleja el ajuste sintetico `applied`."""

    async def test_ca_03_18_retroactive_initial_load_shifts_monthly_amounts(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        today = datetime.now(UTC).date()
        current_amount_since = date(today.year, today.month, 1)

        created = await client.post(
            "/v1/contracts",
            json={
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "currency": "ARS",
                "initial_amount": "100000.00",
                "start_date": (today - timedelta(days=240)).isoformat(),
                "end_date": (today + timedelta(days=365)).isoformat(),
                "daily_late_fee_pct": "0.1",
                "current_amount": "150000.00",
                "current_amount_since": current_amount_since.isoformat(),
            },
            headers=owner["headers"],
        )
        assert created.status_code == 201
        contract_id = created.json()["data"]["id"]

        response = await client.get(f"/v1/contracts/{contract_id}", headers=owner["headers"])

        assert response.status_code == 200
        rows = response.json()["data"]["monthly_amounts"]
        by_period = {row["period"]: row["amount"] for row in rows}
        assert by_period[current_amount_since.isoformat()] == "150000.00"
        # Un mes anterior a la carga inicial sigue en initial_amount.
        earliest_period = min(by_period)
        if earliest_period != current_amount_since.isoformat():
            assert by_period[earliest_period] == "100000.00"


class TestCA0319GetTerminatedContractCutsSeries:
    """CA-03-19: contrato `terminated` corta `monthly_amounts[]` en el mes
    de la terminacion efectiva (evento `contract.terminated` de
    `audit_logs`), no en `end_date`."""

    async def test_ca_03_19_terminated_contract_cuts_at_termination_month(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        today = datetime.now(UTC).date()
        contract_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            currency="ARS",
            initial_amount="100000.00",
            start_date=(today - timedelta(days=180)).isoformat(),
            end_date=(today + timedelta(days=365 * 2)).isoformat(),  # vigencia pactada, futura
            status="active",
        )

        terminate = await client.post(
            f"/v1/contracts/{contract_id}/terminate",
            json={"reason": "Mudanza anticipada del inquilino"},
            headers=owner["headers"],
        )
        assert terminate.status_code == 200

        response = await client.get(f"/v1/contracts/{contract_id}", headers=owner["headers"])

        assert response.status_code == 200
        rows = response.json()["data"]["monthly_amounts"]
        current_month = date(today.year, today.month, 1).isoformat()
        # El corte es el mes de la terminacion (hoy, en este test), no
        # `end_date` (2 anios en el futuro) -- ningun periodo posterior al
        # mes actual aparece en la serie.
        assert rows[0]["period"] == current_month
        assert all(row["period"] <= current_month for row in rows)


class TestCA0320GetContractStartingThisMonth:
    """CA-03-20: contrato cuyo `start_date` cae en el mes actual --
    `monthly_amounts` con exactamente 1 elemento."""

    async def test_ca_03_20_contract_starting_this_month_returns_single_element(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        today = datetime.now(UTC).date()
        start_date = date(today.year, today.month, 1)
        contract_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            currency="ARS",
            initial_amount="80000.00",
            start_date=start_date.isoformat(),
            end_date=(today + timedelta(days=365)).isoformat(),
            status="draft",
        )

        response = await client.get(f"/v1/contracts/{contract_id}", headers=owner["headers"])

        assert response.status_code == 200
        rows = response.json()["data"]["monthly_amounts"]
        assert len(rows) == 1
        assert rows[0]["period"] == start_date.isoformat()
        assert rows[0]["amount"] == "80000.00"


class TestCA0321GetContractDescendingOrder:
    """CA-03-21: `monthly_amounts[]` viene siempre en orden estrictamente
    descendente por `period`."""

    async def test_ca_03_21_monthly_amounts_are_strictly_descending(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        today = datetime.now(UTC).date()
        contract_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            currency="ARS",
            initial_amount="60000.00",
            start_date=(today - timedelta(days=300)).isoformat(),
            end_date=(today + timedelta(days=365)).isoformat(),
            status="active",
        )

        response = await client.get(f"/v1/contracts/{contract_id}", headers=owner["headers"])

        assert response.status_code == 200
        periods = [row["period"] for row in response.json()["data"]["monthly_amounts"]]
        assert periods == sorted(periods, reverse=True)
        assert len(periods) == len(set(periods))


class TestCA0322GetUsdContractFlatSeries:
    """CA-03-22: contrato USD sin carga inicial -- serie plana en
    `initial_amount` (RN-03/RN-C02)."""

    async def test_ca_03_22_usd_contract_without_initial_load_is_flat(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        today = datetime.now(UTC).date()
        contract_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            currency="USD",
            initial_amount="500.00",
            start_date=(today - timedelta(days=120)).isoformat(),
            end_date=(today + timedelta(days=365)).isoformat(),
            status="active",
        )

        response = await client.get(f"/v1/contracts/{contract_id}", headers=owner["headers"])

        assert response.status_code == 200
        rows = response.json()["data"]["monthly_amounts"]
        assert len(rows) >= 1
        assert all(row["amount"] == "500.00" for row in rows)
