"""tests/integration/contracts/test_rent_period_hook.py

SDD: spec_module_03_contratos.md §RF-04 paso 4 (RN-P01) + core/sdd_02_domain_model.md
§2.8 ("mientras exista un ajuste `pending`, su Periodo de Alquiler del mes
de ajuste no se genera hasta aplicar el %").

Cubre `contract_has_pending_adjustment_for_period` -- el guard reusable
que el futuro job mensual `generate_rent_periods` (issue #21) debe
consultar antes de generar el `rent_period` de un contrato/periodo. La
tabla `rent_periods` todavia no existe (issue #20/#21); este test verifica
la condicion en si misma contra `contract_adjustments`, que ya existe.
"""

from __future__ import annotations

from datetime import date

import pytest

from adminprop.db.session import get_session_factory
from adminprop.modules.contracts.rent_period_hook import (
    contract_has_pending_adjustment_for_period,
)

pytestmark = pytest.mark.asyncio


async def _seed_org_with_owner(seed):
    org = await seed.create_organization_with_system_roles()
    owner = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["owner"],
        role_name="owner",
    )
    return owner


async def _seed_property_and_renter(seed, organization_id):
    landlord_id = await seed.create_landlord_row(organization_id=organization_id)
    property_id = await seed.create_property_row(
        organization_id=organization_id, landlord_id=landlord_id
    )
    renter_id = await seed.create_renter_row(organization_id=organization_id)
    return property_id, renter_id


class TestRnP01ContractHasPendingAdjustmentForPeriod:
    """RN-P01: "el rent_period del mes de ajuste NO se genera hasta que el
    ajuste este aplicado"."""

    async def test_returns_true_when_pending_adjustment_matches_period(self, seed):
        owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        contract_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            status="active",
        )
        await seed.create_adjustment_row(
            organization_id=owner["organization_id"],
            contract_id=contract_id,
            due_period="2026-04-01",
            status="pending",
        )

        session_factory = get_session_factory()
        async with session_factory() as session:
            result = await contract_has_pending_adjustment_for_period(
                session,
                contract_id=contract_id,
                organization_id=owner["organization_id"],
                period=date(2026, 4, 1),
            )

        assert result is True

    async def test_returns_false_when_adjustment_for_period_is_already_applied(self, seed):
        owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        contract_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            status="active",
        )
        await seed.create_adjustment_row(
            organization_id=owner["organization_id"],
            contract_id=contract_id,
            due_period="2026-04-01",
            status="applied",
            pct_applied="10.00",
            new_amount="110000.00",
            applied_by=owner["id"],
        )

        session_factory = get_session_factory()
        async with session_factory() as session:
            result = await contract_has_pending_adjustment_for_period(
                session,
                contract_id=contract_id,
                organization_id=owner["organization_id"],
                period=date(2026, 4, 1),
            )

        assert result is False

    async def test_returns_false_when_pending_adjustment_is_for_a_different_period(self, seed):
        owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        contract_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            status="active",
        )
        await seed.create_adjustment_row(
            organization_id=owner["organization_id"],
            contract_id=contract_id,
            due_period="2026-07-01",
            status="pending",
        )

        session_factory = get_session_factory()
        async with session_factory() as session:
            result = await contract_has_pending_adjustment_for_period(
                session,
                contract_id=contract_id,
                organization_id=owner["organization_id"],
                period=date(2026, 4, 1),
            )

        assert result is False

    async def test_returns_false_when_contract_has_no_adjustments_at_all(self, seed):
        owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        contract_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            status="active",
        )

        session_factory = get_session_factory()
        async with session_factory() as session:
            result = await contract_has_pending_adjustment_for_period(
                session,
                contract_id=contract_id,
                organization_id=owner["organization_id"],
                period=date(2026, 4, 1),
            )

        assert result is False

    async def test_returns_false_for_another_contracts_pending_adjustment(self, seed):
        """El guard filtra explicitamente por `contract_id` -- un ajuste
        pendiente de OTRO contrato en el mismo periodo no debe afectar."""
        owner = await _seed_org_with_owner(seed)
        property_a, renter_a = await _seed_property_and_renter(seed, owner["organization_id"])
        property_b, renter_b = await _seed_property_and_renter(seed, owner["organization_id"])
        contract_a = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_a,
            renter_id=renter_a,
            status="active",
        )
        contract_b = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_b,
            renter_id=renter_b,
            status="active",
        )
        await seed.create_adjustment_row(
            organization_id=owner["organization_id"],
            contract_id=contract_b,
            due_period="2026-04-01",
            status="pending",
        )

        session_factory = get_session_factory()
        async with session_factory() as session:
            result = await contract_has_pending_adjustment_for_period(
                session,
                contract_id=contract_a,
                organization_id=owner["organization_id"],
                period=date(2026, 4, 1),
            )

        assert result is False
