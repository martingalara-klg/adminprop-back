"""Issue #29 -- documents_worker.generate_settlement: cuerpo async real
contra Postgres (no mockeado -- ver tests/unit/workers/
test_documents_worker.py para la version mockeada).

Reemplaza el test del esqueleto (issue #4), que solo probaba que
`tenant_scoped_session` funcionaba end-to-end sin tabla de negocio.

Requiere Postgres real con `alembic upgrade head` ya corrido -- mismo
patron que tests/integration/workers/test_notification_worker_outbox.py.
Se invoca `_generate_settlement_async` directamente (no el wrapper
Celery sincronico) -- mismo criterio que ese archivo.

SDD: docs/sdd/features/spec_module_05_liquidaciones.md §RF-01/RF-02.
Implements: CA-05-01, CA-05-02, CA-05-03, CA-05-04/CA-04-08, RN-L04.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_session_factory, set_tenant_context
from adminprop.modules.settlements.job_status import get_job_status
from adminprop.modules.settlements.repository import SettlementRepository
from adminprop.workers.documents_worker import _generate_settlement_async

pytestmark = pytest.mark.asyncio


class _Ctx:
    """Helper minimo para sembrar el escenario CA-05-01 (2 propiedades de
    un mismo propietario) sin repetir el boilerplate en cada test."""

    def __init__(self, seed):
        self.seed = seed

    async def build(self, *, commission_pct: str = "10.00"):
        org_id = await self.seed.create_organization()
        user = await self.seed.create_user()
        landlord_id = await self.seed.create_landlord_row(
            organization_id=org_id, commission_pct=commission_pct
        )
        renter_id = await self.seed.create_renter_row(organization_id=org_id)
        property_a = await self.seed.create_property_row(
            organization_id=org_id, landlord_id=landlord_id, address="Prop A"
        )
        property_b = await self.seed.create_property_row(
            organization_id=org_id, landlord_id=landlord_id, address="Prop B"
        )
        contract_a = await self.seed.create_contract_row(
            organization_id=org_id, property_id=property_a, renter_id=renter_id
        )
        contract_b = await self.seed.create_contract_row(
            organization_id=org_id, property_id=property_b, renter_id=renter_id
        )
        return {
            "org_id": org_id,
            "user_id": user["id"],
            "landlord_id": landlord_id,
            "property_a": property_a,
            "property_b": property_b,
            "contract_a": contract_a,
            "contract_b": contract_b,
        }


async def _create_placeholder_settlement(
    org_id: uuid.UUID,
    landlord_id: uuid.UUID,
    *,
    generated_by: uuid.UUID,
    commission_pct: Decimal,
    exchange_rate: Decimal | None = None,
    period: str = "2026-06-01",
) -> uuid.UUID:
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, org_id)
        repo = SettlementRepository(session)
        settlement = await repo.create_placeholder(
            organization_id=org_id,
            landlord_id=landlord_id,
            period=date.fromisoformat(period),
            exchange_rate=exchange_rate,
            commission_pct_used=commission_pct,
            generated_by=generated_by,
        )
        return settlement.id


class TestCa0501FormulaEndToEnd:
    """CA-05-01: 2 propiedades ARS -- neto = cobros - comision - cargos -
    reparaciones, a centavo (redondeo half-even)."""

    async def test_generate_settlement_computes_totals_and_line_items(self, seed):
        ctx = await _Ctx(seed).build(commission_pct="10.00")

        rent_period_a = await seed.create_rent_period_row(
            organization_id=ctx["org_id"],
            contract_id=ctx["contract_a"],
            period="2026-06-01",
            status="paid",
        )
        rent_period_b = await seed.create_rent_period_row(
            organization_id=ctx["org_id"],
            contract_id=ctx["contract_b"],
            period="2026-06-01",
            status="paid",
        )
        await seed.create_payment_row(
            organization_id=ctx["org_id"],
            rent_period_id=rent_period_a,
            created_by=ctx["user_id"],
            amount="100000.00",
        )
        await seed.create_payment_row(
            organization_id=ctx["org_id"],
            rent_period_id=rent_period_b,
            created_by=ctx["user_id"],
            amount="80000.00",
            charged_interest="1500.50",
        )
        recurring_charge = await seed.create_recurring_charge_row(
            organization_id=ctx["org_id"], property_id=ctx["property_a"]
        )
        await seed.create_charge_entry_row(
            organization_id=ctx["org_id"],
            recurring_charge_id=recurring_charge,
            created_by=ctx["user_id"],
            amount="5000.00",
        )
        work_order_id = await seed.create_work_order_row(
            organization_id=ctx["org_id"],
            property_id=ctx["property_b"],
            created_by=ctx["user_id"],
            final_cost="2000.00",
        )

        settlement_id = await _create_placeholder_settlement(
            ctx["org_id"],
            ctx["landlord_id"],
            generated_by=ctx["user_id"],
            commission_pct=Decimal("10.00"),
        )

        await _generate_settlement_async(settlement_id, ctx["org_id"], "req-ca-05-01")

        row = await seed.get_settlement_row(settlement_id)
        total_collected = Decimal("100000.00") + Decimal("80000.00") + Decimal("1500.50")
        commission_total = (total_collected * Decimal("10.00") / 100).quantize(Decimal("0.01"))
        expected_net = (
            total_collected - commission_total - Decimal("5000.00") - Decimal("2000.00")
        )

        assert Decimal(row["total_collected"]) == total_collected.quantize(Decimal("0.01"))
        assert Decimal(row["commission_total"]) == commission_total
        assert Decimal(row["charges_total"]) == Decimal("5000.00")
        assert Decimal(row["repairs_total"]) == Decimal("2000.00")
        assert Decimal(row["net_amount"]) == expected_net.quantize(Decimal("0.01"))

        job = await get_job_status(settlement_id)
        assert job["status"] == "completed"
        assert job["warnings"] == []

        # RN-L04: la reparacion queda vinculada, no se vuelve a traer.
        work_order_row = await seed.get_work_order_row(work_order_id)
        assert str(work_order_row["settled_in_settlement_id"]) == str(settlement_id)


class TestCa0502UsdConversionEndToEnd:
    async def test_usd_payment_is_converted_to_ars_with_the_settlement_exchange_rate(
        self, seed
    ):
        ctx = await _Ctx(seed).build()
        rent_period = await seed.create_rent_period_row(
            organization_id=ctx["org_id"],
            contract_id=ctx["contract_a"],
            period="2026-06-01",
            currency="USD",
            status="paid",
        )
        await seed.create_payment_row(
            organization_id=ctx["org_id"],
            rent_period_id=rent_period,
            created_by=ctx["user_id"],
            payment_currency="USD",
            amount="500.00",
        )

        settlement_id = await _create_placeholder_settlement(
            ctx["org_id"],
            ctx["landlord_id"],
            generated_by=ctx["user_id"],
            commission_pct=Decimal("10.00"),
            exchange_rate=Decimal("1000.0000"),
        )

        await _generate_settlement_async(settlement_id, ctx["org_id"], "req-ca-05-02")

        row = await seed.get_settlement_row(settlement_id)
        assert Decimal(row["total_collected"]) == Decimal("500000.00")

        session_factory = get_session_factory()
        async with session_factory() as session:
            result = await session.execute(
                sa.text(
                    "SELECT original_amount, original_currency, amount_ars "
                    "FROM settlement_line_items "
                    "WHERE settlement_id = :id AND line_type = 'rent_collected'"
                ),
                {"id": str(settlement_id)},
            )
            line = result.mappings().one()
            assert Decimal(line["original_amount"]) == Decimal("500.00")
            assert line["original_currency"] == "USD"
            assert Decimal(line["amount_ars"]) == Decimal("500000.00")


class TestCa0503WithErrorsEndToEnd:
    async def test_unpaid_period_produces_with_errors_job_status(self, seed):
        ctx = await _Ctx(seed).build()
        await seed.create_rent_period_row(
            organization_id=ctx["org_id"],
            contract_id=ctx["contract_a"],
            period="2026-06-01",
            status="pending",
        )

        settlement_id = await _create_placeholder_settlement(
            ctx["org_id"],
            ctx["landlord_id"],
            generated_by=ctx["user_id"],
            commission_pct=Decimal("10.00"),
        )

        await _generate_settlement_async(settlement_id, ctx["org_id"], "req-ca-05-03")

        job = await get_job_status(settlement_id)
        assert job["status"] == "with_errors"
        assert len(job["warnings"]) >= 1

        row = await seed.get_settlement_row(settlement_id)
        assert row["status"] == "draft"


class TestCa0504AlreadySettledEndToEnd:
    async def test_landlord_account_payment_is_already_settled_and_pays_commission(
        self, seed
    ):
        ctx = await _Ctx(seed).build()
        rent_period = await seed.create_rent_period_row(
            organization_id=ctx["org_id"], contract_id=ctx["contract_a"], period="2026-06-01"
        )
        await seed.create_payment_row(
            organization_id=ctx["org_id"],
            rent_period_id=rent_period,
            created_by=ctx["user_id"],
            amount="50000.00",
            destination="landlord_account",
        )

        settlement_id = await _create_placeholder_settlement(
            ctx["org_id"],
            ctx["landlord_id"],
            generated_by=ctx["user_id"],
            commission_pct=Decimal("10.00"),
        )

        await _generate_settlement_async(settlement_id, ctx["org_id"], "req-ca-05-04")

        row = await seed.get_settlement_row(settlement_id)
        assert Decimal(row["total_collected"]) == Decimal("0.00")
        assert Decimal(row["already_settled_total"]) == Decimal("50000.00")
        assert Decimal(row["commission_total"]) == Decimal("5000.00")
