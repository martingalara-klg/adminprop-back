"""tests/integration/contracts/test_initial_load_retroactive.py

SDD: docs/sdd/features/spec_module_03_contratos.md RF-02/RN-11 (issue #119)
     + core/sdd_02_domain_model.md §3 RN-P09.
Implements: CA-03-26, CA-03-27, CA-03-28, CA-03-29, CA-03-30.

Feedback #3 del PO (2026-08-29): al dar de alta un contrato en curso, los
meses transcurridos deben figurar en cobranzas como ya cobrados. Ejercita
`POST /v1/contracts` end-to-end -- `ContractService.create` ->
`contracts/rent_period_hook.py.generate_initial_load_history` -- y
verifica los `rent_periods`/`payments` generados directo en DB (mismo
patron que `test_contracts_crud.py.TestCA0309HistoricalAmountsOneElapsedTramo`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_session_factory
from adminprop.modules.payments.repository import PaymentRepository, RentPeriodRepository

pytestmark = pytest.mark.asyncio


def _months_ago(anchor: date, months: int) -> date:
    """Dia 1 del mes que cae `months` meses antes de `anchor` -- mismo
    helper que `test_contracts_crud.py` (duplicado a proposito, ver el
    encabezado de `conftest.py`)."""
    zero_based_month = anchor.month - 1 - months
    year = anchor.year + zero_based_month // 12
    month = zero_based_month % 12 + 1
    return date(year, month, 1)


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


async def _rent_periods_for_contract(contract_id: uuid.UUID, organization_id: uuid.UUID) -> list:
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        result = await session.execute(
            sa.text(
                "SELECT id, period, amount_due, status, paid_total FROM rent_periods "
                "WHERE contract_id = :contract_id AND organization_id = :org_id "
                "ORDER BY period ASC"
            ),
            {"contract_id": str(contract_id), "org_id": str(organization_id)},
        )
        return [dict(row._mapping) for row in result]


async def _payments_for_rent_period(rent_period_id: uuid.UUID, organization_id: uuid.UUID) -> list:
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        payments = await PaymentRepository(session).list_by_rent_period(
            rent_period_id, organization_id
        )
        return payments


class TestCA0326RetroactivePeriodsWithHistoricalAmounts:
    """CA-03-26: contrato iniciado hace N meses con `historical_amounts[]`
    -> N periodos pasados `paid` con montos por tramo + N cobros
    `initial_load`."""

    async def test_ca_03_26_three_elapsed_months_generate_three_paid_periods(
        self, client, seed
    ):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        today = datetime.now(UTC).date()
        start_date = _months_ago(today, 3)

        created = await client.post(
            "/v1/contracts",
            json={
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "currency": "ARS",
                "initial_amount": "100000.00",
                "start_date": start_date.isoformat(),
                "end_date": (today + timedelta(days=365)).isoformat(),
                "daily_late_fee_pct": "0.1",
                "adjustment_frequency_months": 1,
                "adjustment_index": "icl",
                "historical_amounts": ["100000.00", "110000.00", "120000.00", "130000.00"],
            },
            headers=owner["headers"],
        )
        assert created.status_code == 201
        contract_id = uuid.UUID(created.json()["data"]["id"])

        rent_periods = await _rent_periods_for_contract(contract_id, owner["organization_id"])
        # 3 periodos retroactivos (start, start+1mo, start+2mo) -- el mes
        # actual (start+3mo) NO se genera aca (nace pending al activar).
        assert len(rent_periods) == 3
        expected_amounts = ["100000.00", "110000.00", "120000.00"]
        for row, expected_amount in zip(rent_periods, expected_amounts, strict=True):
            assert row["status"] == "paid"
            assert str(row["amount_due"]) == expected_amount
            assert str(row["paid_total"]) == expected_amount

            payments = await _payments_for_rent_period(row["id"], owner["organization_id"])
            assert len(payments) == 1
            payment = payments[0]
            assert payment.origin == "initial_load"
            assert str(payment.amount) == expected_amount
            assert payment.destination == "landlord_account"
            assert payment.charged_interest == Decimal("0.00")
            assert payment.suggested_interest == Decimal("0.00")
            assert payment.forgiven_interest == Decimal("0.00")
            assert payment.exchange_rate is None
            assert payment.payment_date == row["period"]
            assert payment.notes == (
                "Cobro registrado automáticamente al dar de alta el contrato en curso."
            )


class TestCA0327RetroactivePeriodWithoutTramos:
    """CA-03-27: contrato iniciado el mes pasado SIN `historical_amounts`/
    `current_amount` (ningun tramo declarado) genera igual 1 periodo
    retroactivo `paid` con `initial_amount`."""

    async def test_ca_03_27_started_last_month_without_declared_tramos(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        today = datetime.now(UTC).date()
        start_date = _months_ago(today, 1)

        created = await client.post(
            "/v1/contracts",
            json={
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "currency": "ARS",
                "initial_amount": "50000.00",
                "start_date": start_date.isoformat(),
                "end_date": (today + timedelta(days=365)).isoformat(),
                "daily_late_fee_pct": "0.1",
            },
            headers=owner["headers"],
        )
        assert created.status_code == 201
        contract_id = uuid.UUID(created.json()["data"]["id"])

        rent_periods = await _rent_periods_for_contract(contract_id, owner["organization_id"])
        assert len(rent_periods) == 1
        assert rent_periods[0]["status"] == "paid"
        assert str(rent_periods[0]["amount_due"]) == "50000.00"
        assert rent_periods[0]["period"] == start_date

        payments = await _payments_for_rent_period(rent_periods[0]["id"], owner["organization_id"])
        assert len(payments) == 1
        assert payments[0].origin == "initial_load"
        assert str(payments[0].amount) == "50000.00"


class TestCA0328CurrentMonthStillPending:
    """CA-03-28: el mes actual del contrato recien creado sigue naciendo
    `pending` al activarse -- sin cambios respecto del comportamiento
    previo (RF-03)."""

    async def test_ca_03_28_current_month_still_pending_after_activate(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        today = datetime.now(UTC).date()
        start_date = _months_ago(today, 2)

        created = await client.post(
            "/v1/contracts",
            json={
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "currency": "ARS",
                "initial_amount": "80000.00",
                "start_date": start_date.isoformat(),
                "end_date": (today + timedelta(days=365)).isoformat(),
                "daily_late_fee_pct": "0.1",
            },
            headers=owner["headers"],
        )
        assert created.status_code == 201
        contract_id = created.json()["data"]["id"]

        activated = await client.post(
            f"/v1/contracts/{contract_id}/activate", headers=owner["headers"]
        )
        assert activated.status_code == 200

        current_month = date(today.year, today.month, 1)
        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
            current_period = await RentPeriodRepository(session).get_by_contract_and_period(
                uuid.UUID(contract_id), owner["organization_id"], current_month
            )
        assert current_period is not None
        assert current_period.status == "pending"
        assert str(current_period.amount_due) == "80000.00"

        rent_periods = await _rent_periods_for_contract(
            uuid.UUID(contract_id), owner["organization_id"]
        )
        # 2 retroactivos (paid) + 1 actual (pending) = 3 en total.
        assert len(rent_periods) == 3
        assert sum(1 for r in rent_periods if r["status"] == "paid") == 2
        assert sum(1 for r in rent_periods if r["status"] == "pending") == 1


class TestCA0329NormalCreationUnaffected:
    """CA-03-29: alta normal (contrato que arranca este mes) no genera
    ningun periodo ni cobro retroactivo."""

    async def test_ca_03_29_normal_creation_this_month_generates_nothing(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        today = datetime.now(UTC).date()
        start_date = date(today.year, today.month, 1)

        created = await client.post(
            "/v1/contracts",
            json={
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "currency": "ARS",
                "initial_amount": "90000.00",
                "start_date": start_date.isoformat(),
                "end_date": (today + timedelta(days=365)).isoformat(),
                "daily_late_fee_pct": "0.1",
            },
            headers=owner["headers"],
        )
        assert created.status_code == 201
        contract_id = uuid.UUID(created.json()["data"]["id"])

        rent_periods = await _rent_periods_for_contract(contract_id, owner["organization_id"])
        assert rent_periods == []

        rows = await seed.audit_rows(owner["organization_id"], "contract.initial_load_generated")
        assert rows == []


class TestCA0330AuditedSummaryEvent:
    """CA-03-30: la carga retroactiva queda auditada con un evento resumen
    (`contract.initial_load_generated`) con la cantidad de periodos/cobros
    generados y el autor."""

    async def test_ca_03_30_initial_load_generates_summary_audit_event(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        today = datetime.now(UTC).date()
        start_date = _months_ago(today, 2)

        created = await client.post(
            "/v1/contracts",
            json={
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "currency": "ARS",
                "initial_amount": "60000.00",
                "start_date": start_date.isoformat(),
                "end_date": (today + timedelta(days=365)).isoformat(),
                "daily_late_fee_pct": "0.1",
            },
            headers=owner["headers"],
        )
        assert created.status_code == 201
        contract_id = created.json()["data"]["id"]

        rows = await seed.audit_rows(owner["organization_id"], "contract.initial_load_generated")
        assert len(rows) == 1
        assert rows[0]["entity_id"] == uuid.UUID(contract_id)
        assert rows[0]["user_id"] == owner["id"]
        assert rows[0]["after_state"]["periods_generated"] == 2
        assert rows[0]["after_state"]["first_period"] == start_date.isoformat()
