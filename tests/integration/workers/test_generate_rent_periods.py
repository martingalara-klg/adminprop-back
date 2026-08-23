"""Issue #21 -- `generate_rent_periods`: cuerpo async real contra Postgres.

SDD: spec_module_04_cobranzas.md §RF-01 + core/sdd_04_nonfunctional.md
§1.3 (Beat itera TODAS las organizaciones `active`, 1° de cada mes, job
idempotente).
Implements: CA-04-01 (idempotencia), CA-04-02 (RN-P01).

Mismo criterio que tests/integration/workers/test_detect_due_adjustments.py:
se invoca `_generate_rent_periods_async` directamente (no el wrapper
Celery sincronico, que hace `asyncio.run()` y no puede llamarse desde un
test ya corriendo dentro del loop de pytest-asyncio).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_session_factory
from adminprop.workers.notification_worker import _generate_rent_periods_async

pytestmark = pytest.mark.asyncio

_FAR_END_DATE = date(2040, 1, 1)


async def _seed_organization(*, status: str = "active") -> uuid.UUID:
    org_id = uuid.uuid4()
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        await session.execute(
            sa.text(
                "INSERT INTO organizations (id, slug, name, status) "
                "VALUES (:id, :slug, :name, :status)"
            ),
            {
                "id": str(org_id),
                "slug": f"org-{org_id.hex[:8]}",
                "name": f"Org {org_id.hex[:8]}",
                "status": status,
            },
        )
    return org_id


async def _seed_contract(
    *,
    organization_id: uuid.UUID,
    currency: str = "ARS",
    status: str = "active",
    start_date: str = "2026-01-01",
    current_amount: str = "100000.00",
) -> uuid.UUID:
    landlord_id = uuid.uuid4()
    property_id = uuid.uuid4()
    renter_id = uuid.uuid4()
    contract_id = uuid.uuid4()
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        await session.execute(
            sa.text(
                "INSERT INTO landlords (id, organization_id, name, commission_pct) "
                "VALUES (:id, :org_id, 'Propietario', '10.00')"
            ),
            {"id": str(landlord_id), "org_id": str(organization_id)},
        )
        await session.execute(
            sa.text(
                "INSERT INTO properties "
                "(id, organization_id, landlord_id, address, property_type, status) "
                "VALUES (:id, :org_id, :landlord_id, 'Direccion de prueba', "
                "'departamento', 'rented')"
            ),
            {
                "id": str(property_id),
                "org_id": str(organization_id),
                "landlord_id": str(landlord_id),
            },
        )
        await session.execute(
            sa.text(
                "INSERT INTO renters (id, organization_id, name) VALUES (:id, :org_id, 'Inquilino')"
            ),
            {"id": str(renter_id), "org_id": str(organization_id)},
        )
        await session.execute(
            sa.text(
                "INSERT INTO contracts "
                "(id, organization_id, property_id, renter_id, currency, initial_amount, "
                "current_amount, start_date, end_date, daily_late_fee_pct, status) "
                "VALUES (:id, :org_id, :property_id, :renter_id, :currency, :amount, :amount, "
                ":start_date, :end_date, '0.1', :status)"
            ),
            {
                "id": str(contract_id),
                "org_id": str(organization_id),
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "currency": currency,
                "amount": current_amount,
                "start_date": date.fromisoformat(start_date),
                "end_date": _FAR_END_DATE,
                "status": status,
            },
        )
    return contract_id


async def _seed_pending_adjustment(
    *, organization_id: uuid.UUID, contract_id: uuid.UUID, due_period: str
) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        await session.execute(
            sa.text(
                "INSERT INTO contract_adjustments "
                "(id, organization_id, contract_id, due_period, status, previous_amount) "
                "VALUES (:id, :org_id, :contract_id, :due_period, 'pending', '100000.00')"
            ),
            {
                "id": str(uuid.uuid4()),
                "org_id": str(organization_id),
                "contract_id": str(contract_id),
                "due_period": date.fromisoformat(due_period),
            },
        )


async def _rent_periods(organization_id: uuid.UUID, contract_id: uuid.UUID) -> list[dict]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        result = await session.execute(
            sa.text(
                "SELECT id, period, amount_due, currency, status FROM rent_periods "
                "WHERE organization_id = :org_id AND contract_id = :contract_id"
            ),
            {"org_id": str(organization_id), "contract_id": str(contract_id)},
        )
        return [dict(row._mapping) for row in result]


def _current_month() -> date:
    """Mismo criterio que `RentPeriodService.generate_monthly`: el job
    corre con `today = datetime.now(UTC).date()` real -- los tests
    calculan el mes esperado dinamicamente en vez de fijar una fecha."""
    today = datetime.now(UTC).date()
    return date(today.year, today.month, 1)


class TestCA0401GenerateRentPeriods:
    """CA-04-01: "El 1° del mes, cada contrato activo tiene su rent_period
    `pending` con el monto vigente; re-ejecutar el job no duplica
    ninguno"."""

    async def test_ca_04_01_creates_pending_rent_period_for_active_contract(self):
        org_id = await _seed_organization()
        contract_id = await _seed_contract(organization_id=org_id, current_amount="150000.00")

        await _generate_rent_periods_async(request_id="req-rent-1")

        rows = await _rent_periods(org_id, contract_id)
        assert len(rows) == 1
        assert rows[0]["period"] == _current_month()
        assert str(rows[0]["amount_due"]) == "150000.00"
        assert rows[0]["currency"] == "ARS"
        assert rows[0]["status"] == "pending"

    async def test_ca_04_01_second_run_same_month_does_not_duplicate(self):
        """Idempotencia: re-correr el job el mismo mes no duplica el
        rent_period ya creado."""
        org_id = await _seed_organization()
        contract_id = await _seed_contract(organization_id=org_id)

        await _generate_rent_periods_async(request_id="req-rent-2a")
        await _generate_rent_periods_async(request_id="req-rent-2b")

        rows = await _rent_periods(org_id, contract_id)
        assert len(rows) == 1

    async def test_usd_contract_also_generates_rent_period(self):
        """RN-C: solo el AJUSTE automatico no aplica a USD (RN-C02) -- la
        generacion mensual de `rent_periods` no distingue moneda."""
        org_id = await _seed_organization()
        contract_id = await _seed_contract(
            organization_id=org_id, currency="USD", current_amount="500.00"
        )

        await _generate_rent_periods_async(request_id="req-rent-3")

        rows = await _rent_periods(org_id, contract_id)
        assert len(rows) == 1
        assert rows[0]["currency"] == "USD"

    async def test_draft_contract_is_never_considered(self):
        org_id = await _seed_organization()
        contract_id = await _seed_contract(organization_id=org_id, status="draft")

        await _generate_rent_periods_async(request_id="req-rent-4")

        rows = await _rent_periods(org_id, contract_id)
        assert rows == []

    async def test_terminated_contract_is_never_considered(self):
        org_id = await _seed_organization()
        contract_id = await _seed_contract(organization_id=org_id, status="terminated")

        await _generate_rent_periods_async(request_id="req-rent-5")

        rows = await _rent_periods(org_id, contract_id)
        assert rows == []

    async def test_organization_not_active_is_never_iterated(self):
        org_id = await _seed_organization(status="pending_owner")
        contract_id = await _seed_contract(organization_id=org_id)

        await _generate_rent_periods_async(request_id="req-rent-6")

        rows = await _rent_periods(org_id, contract_id)
        assert rows == []

    async def test_two_organizations_are_isolated_from_each_other(self):
        org_a = await _seed_organization()
        org_b = await _seed_organization()
        contract_a = await _seed_contract(organization_id=org_a, current_amount="100000.00")
        contract_b = await _seed_contract(organization_id=org_b, current_amount="200000.00")

        await _generate_rent_periods_async(request_id="req-rent-7")

        rows_a = await _rent_periods(org_a, contract_a)
        rows_b = await _rent_periods(org_b, contract_b)
        assert len(rows_a) == 1
        assert str(rows_a[0]["amount_due"]) == "100000.00"
        assert len(rows_b) == 1
        assert str(rows_b[0]["amount_due"]) == "200000.00"
        # Ningun rent_period de A aparece en la consulta de B y viceversa
        # (`_rent_periods` ya filtra por organization_id -- doble chequeo
        # de que no hay cruce de filas entre orgs).
        assert all(r["id"] not in {row["id"] for row in rows_b} for r in rows_a)


class TestCA0402GenerateRentPeriodsRespectsPendingAdjustment:
    """CA-04-02: "Un contrato con ajuste pendiente no genera el período del
    mes hasta aplicar el %"."""

    async def test_ca_04_02_contract_with_pending_adjustment_for_current_period_is_skipped(self):
        org_id = await _seed_organization()
        contract_id = await _seed_contract(organization_id=org_id)
        await _seed_pending_adjustment(
            organization_id=org_id,
            contract_id=contract_id,
            due_period=_current_month().isoformat(),
        )

        await _generate_rent_periods_async(request_id="req-rent-8")

        rows = await _rent_periods(org_id, contract_id)
        assert rows == []

    async def test_pending_adjustment_for_a_different_period_does_not_block(self):
        org_id = await _seed_organization()
        contract_id = await _seed_contract(organization_id=org_id)
        await _seed_pending_adjustment(
            organization_id=org_id, contract_id=contract_id, due_period="2030-01-01"
        )

        await _generate_rent_periods_async(request_id="req-rent-9")

        rows = await _rent_periods(org_id, contract_id)
        assert len(rows) == 1
        assert rows[0]["period"] == _current_month()
