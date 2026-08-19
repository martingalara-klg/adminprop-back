"""tests/integration/contracts/test_rent_period_hook.py

SDD: spec_module_03_contratos.md §RF-03 + §RF-04 paso 4 (RN-P01) +
core/sdd_02_domain_model.md §2.8 ("mientras exista un ajuste `pending`,
su Periodo de Alquiler del mes de ajuste no se genera hasta aplicar el
%") + spec_module_04_cobranzas.md §RF-01.
Implements: CA-04-01 (idempotencia), CA-04-02 (RN-P01).

Cubre `contract_has_pending_adjustment_for_period` -- el guard reusable
que tanto los dos hooks de este archivo como el job mensual
`generate_rent_periods` (issue #21, `modules/payments/service.py`)
consultan antes de generar el `rent_period` de un contrato/periodo -- y,
desde el issue #21, tambien `maybe_generate_current_month_rent_period` y
`maybe_generate_rent_period_for_adjustment`, cuyo INSERT real contra
`rent_periods` (issue #20) reemplazo el no-op de los issues #17/#18.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_session_factory
from adminprop.modules.contracts.rent_period_hook import (
    contract_has_pending_adjustment_for_period,
    maybe_generate_current_month_rent_period,
    maybe_generate_rent_period_for_adjustment,
)
from adminprop.modules.payments.repository import RentPeriodRepository

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


class TestCA0401MaybeGenerateCurrentMonthRentPeriod:
    """CA-04-01/RF-03: "al activarse un contrato a mitad de mes, su
    rent_period del mes en curso se genera en el acto"."""

    async def test_ca_04_01_generates_rent_period_when_start_date_already_passed(self, seed):
        owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        contract_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            status="active",
            start_date="2026-01-01",
        )

        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            new_id = await maybe_generate_current_month_rent_period(
                session,
                contract_id=contract_id,
                organization_id=owner["organization_id"],
                start_date=date(2026, 1, 1),
                today=date(2026, 8, 15),
                amount_due=Decimal("100000.00"),
                currency="ARS",
            )
        assert new_id is not None

        async with session_factory() as session:
            row = await RentPeriodRepository(session).get_by_contract_and_period(
                contract_id, owner["organization_id"], date(2026, 8, 1)
            )
        assert row is not None
        assert str(row.amount_due) == "100000.00"
        assert row.currency == "ARS"
        assert row.status == "pending"

    async def test_does_not_generate_when_start_date_is_in_the_future(self, seed):
        owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        contract_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            status="active",
            start_date="2026-09-01",
        )

        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            result = await maybe_generate_current_month_rent_period(
                session,
                contract_id=contract_id,
                organization_id=owner["organization_id"],
                start_date=date(2026, 9, 1),
                today=date(2026, 8, 15),
                amount_due=Decimal("100000.00"),
                currency="ARS",
            )
        assert result is None

    async def test_ca_04_01_second_call_same_month_is_idempotent(self, seed):
        owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        contract_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            status="active",
            start_date="2026-01-01",
        )

        session_factory = get_session_factory()
        for _ in range(2):
            async with session_factory() as session, session.begin():
                await maybe_generate_current_month_rent_period(
                    session,
                    contract_id=contract_id,
                    organization_id=owner["organization_id"],
                    start_date=date(2026, 1, 1),
                    today=date(2026, 8, 15),
                    amount_due=Decimal("100000.00"),
                    currency="ARS",
                )

        async with session_factory() as session:
            result = await session.execute(
                sa.text("SELECT count(*) FROM rent_periods WHERE contract_id = :cid"),
                {"cid": str(contract_id)},
            )
            assert result.scalar_one() == 1

    async def test_ca_04_02_does_not_generate_when_contract_has_pending_adjustment_for_period(
        self, seed
    ):
        owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        contract_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            status="active",
            start_date="2026-01-01",
        )
        await seed.create_adjustment_row(
            organization_id=owner["organization_id"],
            contract_id=contract_id,
            due_period="2026-08-01",
            status="pending",
        )

        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            result = await maybe_generate_current_month_rent_period(
                session,
                contract_id=contract_id,
                organization_id=owner["organization_id"],
                start_date=date(2026, 1, 1),
                today=date(2026, 8, 15),
                amount_due=Decimal("100000.00"),
                currency="ARS",
            )
        assert result is None

        async with session_factory() as session:
            row = await RentPeriodRepository(session).get_by_contract_and_period(
                contract_id, owner["organization_id"], date(2026, 8, 1)
            )
        assert row is None


class TestCA0402MaybeGenerateRentPeriodForAdjustment:
    """CA-04-02: "al aplicarlo, el período nace con el monto nuevo"."""

    async def test_ca_04_02_generates_rent_period_with_new_amount(self, seed):
        owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        contract_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            status="active",
        )

        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            new_id = await maybe_generate_rent_period_for_adjustment(
                session,
                contract_id=contract_id,
                organization_id=owner["organization_id"],
                period=date(2026, 4, 1),
                amount_due=Decimal("110000.00"),
                currency="ARS",
            )
        assert new_id is not None

        async with session_factory() as session:
            row = await RentPeriodRepository(session).get_by_contract_and_period(
                contract_id, owner["organization_id"], date(2026, 4, 1)
            )
        assert row is not None
        assert str(row.amount_due) == "110000.00"
        assert row.currency == "ARS"
        assert row.status == "pending"

    async def test_ca_04_02_idempotent_if_period_already_exists(self, seed):
        owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        contract_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            status="active",
        )

        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await maybe_generate_rent_period_for_adjustment(
                session,
                contract_id=contract_id,
                organization_id=owner["organization_id"],
                period=date(2026, 4, 1),
                amount_due=Decimal("110000.00"),
                currency="ARS",
            )

        async with session_factory() as session, session.begin():
            second = await maybe_generate_rent_period_for_adjustment(
                session,
                contract_id=contract_id,
                organization_id=owner["organization_id"],
                period=date(2026, 4, 1),
                amount_due=Decimal("999999.00"),
                currency="ARS",
            )
        assert second is None

        async with session_factory() as session:
            row = await RentPeriodRepository(session).get_by_contract_and_period(
                contract_id, owner["organization_id"], date(2026, 4, 1)
            )
        # RN-P01: el periodo ya existente NO se pisa con un monto distinto.
        assert str(row.amount_due) == "110000.00"
