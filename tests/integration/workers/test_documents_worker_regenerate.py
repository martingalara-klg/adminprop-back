"""Issue #30 -- documents_worker._regenerate_settlement_async: cuerpo
async real contra Postgres (mismo criterio que
tests/integration/workers/test_documents_worker.py para #29).

SDD: docs/sdd/features/spec_module_05_liquidaciones.md §RF-03, RN-L03/L04.
Implements: CA-05-05 (reparacion no se descuenta dos veces), CA-05-06
(regeneracion recomputa totales, `regenerated_count++`, auditoria).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from adminprop.db.session import get_session_factory, set_tenant_context
from adminprop.modules.settlements.job_status import get_job_status
from adminprop.modules.settlements.repository import SettlementRepository
from adminprop.workers.documents_worker import (
    _generate_settlement_async,
    _regenerate_settlement_async,
)

pytestmark = pytest.mark.asyncio


class _Ctx:
    def __init__(self, seed):
        self.seed = seed

    async def build(self, *, commission_pct: str = "10.00"):
        org_id = await self.seed.create_organization()
        user = await self.seed.create_user()
        landlord_id = await self.seed.create_landlord_row(
            organization_id=org_id, commission_pct=commission_pct
        )
        renter_id = await self.seed.create_renter_row(organization_id=org_id)
        property_id = await self.seed.create_property_row(
            organization_id=org_id, landlord_id=landlord_id
        )
        contract_id = await self.seed.create_contract_row(
            organization_id=org_id, property_id=property_id, renter_id=renter_id
        )
        return {
            "org_id": org_id,
            "user_id": user["id"],
            "landlord_id": landlord_id,
            "renter_id": renter_id,
            "property_id": property_id,
            "contract_id": contract_id,
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


class TestCa0505RepairNotDoubleDeducted:
    """CA-05-05: "una reparacion closed con payer agency se descuenta en
    la liquidacion y queda vinculada; regenerar no la descuenta dos
    veces" -- RN-L04: la reparacion sigue contando en ESTA liquidacion al
    regenerar (no desaparece), pero nunca se duplica ni la toma otra."""

    async def test_ca_05_05_repair_survives_regeneration_without_double_counting(self, seed):
        ctx = await _Ctx(seed).build()
        rent_period = await seed.create_rent_period_row(
            organization_id=ctx["org_id"], contract_id=ctx["contract_id"], period="2026-06-01"
        )
        await seed.create_payment_row(
            organization_id=ctx["org_id"],
            rent_period_id=rent_period,
            created_by=ctx["user_id"],
            amount="100000.00",
        )
        work_order_id = await seed.create_work_order_row(
            organization_id=ctx["org_id"],
            property_id=ctx["property_id"],
            created_by=ctx["user_id"],
            final_cost="2000.00",
        )

        settlement_id = await _create_placeholder_settlement(
            ctx["org_id"],
            ctx["landlord_id"],
            generated_by=ctx["user_id"],
            commission_pct=Decimal("10.00"),
        )
        await _generate_settlement_async(settlement_id, ctx["org_id"], "req-gen")

        row_after_generate = await seed.get_settlement_row(settlement_id)
        assert Decimal(row_after_generate["repairs_total"]) == Decimal("2000.00")
        work_order_row = await seed.get_work_order_row(work_order_id)
        assert str(work_order_row["settled_in_settlement_id"]) == str(settlement_id)

        # Regenerar sin cambios nuevos -- la reparacion YA vinculada a
        # esta liquidacion debe seguir contando (RN-L04), no desaparecer
        # ni volver a sumarse dos veces.
        await _regenerate_settlement_async(
            settlement_id, ctx["org_id"], "req-regen", None, ctx["user_id"]
        )

        row_after_regenerate = await seed.get_settlement_row(settlement_id)
        assert Decimal(row_after_regenerate["repairs_total"]) == Decimal("2000.00")
        assert row_after_regenerate["regenerated_count"] == 1

        # Solo una linea `repair` en el detalle -- no duplicada.
        line_items = await seed.get_line_items(settlement_id)
        repair_lines = [li for li in line_items if li["line_type"] == "repair"]
        assert len(repair_lines) == 1

    async def test_new_repair_closed_after_generation_is_picked_up_on_regenerate(self, seed):
        """Una reparacion cerrada DESPUES de la generacion original se
        suma recien en la regeneracion (no retroactivamente)."""
        ctx = await _Ctx(seed).build()
        rent_period = await seed.create_rent_period_row(
            organization_id=ctx["org_id"], contract_id=ctx["contract_id"], period="2026-06-01"
        )
        await seed.create_payment_row(
            organization_id=ctx["org_id"],
            rent_period_id=rent_period,
            created_by=ctx["user_id"],
            amount="100000.00",
        )

        settlement_id = await _create_placeholder_settlement(
            ctx["org_id"],
            ctx["landlord_id"],
            generated_by=ctx["user_id"],
            commission_pct=Decimal("10.00"),
        )
        await _generate_settlement_async(settlement_id, ctx["org_id"], "req-gen")

        row_after_generate = await seed.get_settlement_row(settlement_id)
        assert Decimal(row_after_generate["repairs_total"]) == Decimal("0.00")

        await seed.create_work_order_row(
            organization_id=ctx["org_id"],
            property_id=ctx["property_id"],
            created_by=ctx["user_id"],
            final_cost="3000.00",
        )

        await _regenerate_settlement_async(
            settlement_id, ctx["org_id"], "req-regen", None, ctx["user_id"]
        )

        row_after_regenerate = await seed.get_settlement_row(settlement_id)
        assert Decimal(row_after_regenerate["repairs_total"]) == Decimal("3000.00")


class TestCa0506RegenerationRecomputesAndAudits:
    """CA-05-06: "al regenerar, los totales se recomputan,
    regenerated_count incrementa y la auditoria registra el cambio"."""

    async def test_ca_05_06_voided_payment_reduces_total_collected_on_regenerate(self, seed):
        ctx = await _Ctx(seed).build()
        rent_period = await seed.create_rent_period_row(
            organization_id=ctx["org_id"],
            contract_id=ctx["contract_id"],
            period="2026-06-01",
            amount_due="200000.00",
            paid_total="200000.00",
            status="paid",
        )
        payment_a = await seed.create_payment_row(
            organization_id=ctx["org_id"],
            rent_period_id=rent_period,
            created_by=ctx["user_id"],
            amount="100000.00",
        )
        await seed.create_payment_row(
            organization_id=ctx["org_id"],
            rent_period_id=rent_period,
            created_by=ctx["user_id"],
            amount="100000.00",
        )

        settlement_id = await _create_placeholder_settlement(
            ctx["org_id"],
            ctx["landlord_id"],
            generated_by=ctx["user_id"],
            commission_pct=Decimal("10.00"),
        )
        await _generate_settlement_async(settlement_id, ctx["org_id"], "req-gen")

        row_after_generate = await seed.get_settlement_row(settlement_id)
        assert Decimal(row_after_generate["total_collected"]) == Decimal("200000.00")

        # Anular uno de los dos cobros (simula CA-05-06 sin pasar por el
        # endpoint HTTP -- el UPDATE directo es equivalente a
        # `PaymentService.void_payment` para efectos del recalculo).
        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await set_tenant_context(session, ctx["org_id"])
            import sqlalchemy as sa

            await session.execute(
                sa.text("UPDATE payments SET voided_at = now() WHERE id = :id"),
                {"id": str(payment_a)},
            )

        await _regenerate_settlement_async(
            settlement_id, ctx["org_id"], "req-regen", None, ctx["user_id"]
        )

        row_after_regenerate = await seed.get_settlement_row(settlement_id)
        assert Decimal(row_after_regenerate["total_collected"]) == Decimal("100000.00")
        assert row_after_regenerate["regenerated_count"] == 1

        job = await get_job_status(settlement_id)
        assert job["status"] == "completed"

        rows = await seed.audit_rows(ctx["org_id"], "settlement.regenerated")
        assert len(rows) == 1
        assert str(rows[0]["entity_id"]) == str(settlement_id)
        assert str(rows[0]["user_id"]) == str(ctx["user_id"])
        assert rows[0]["before_state"]["total_collected"] == "200000.00"
        assert rows[0]["after_state"]["total_collected"] == "100000.00"
        assert rows[0]["after_state"]["regenerated_count"] == 1

    async def test_regenerate_with_new_exchange_rate_updates_settlement_row(self, seed):
        ctx = await _Ctx(seed).build()
        # Issue #72/RN-L06: la conversion decide por la moneda del
        # CONTRATO, no por `payment_currency`.
        usd_contract = await seed.create_contract_row(
            organization_id=ctx["org_id"],
            property_id=ctx["property_id"],
            renter_id=ctx["renter_id"],
            currency="USD",
        )
        rent_period = await seed.create_rent_period_row(
            organization_id=ctx["org_id"],
            contract_id=usd_contract,
            period="2026-06-01",
            currency="USD",
        )
        await seed.create_payment_row(
            organization_id=ctx["org_id"],
            rent_period_id=rent_period,
            created_by=ctx["user_id"],
            payment_currency="USD",
            amount="100.00",
        )

        settlement_id = await _create_placeholder_settlement(
            ctx["org_id"],
            ctx["landlord_id"],
            generated_by=ctx["user_id"],
            commission_pct=Decimal("10.00"),
            exchange_rate=Decimal("1000.0000"),
        )
        await _generate_settlement_async(settlement_id, ctx["org_id"], "req-gen")
        row_after_generate = await seed.get_settlement_row(settlement_id)
        assert Decimal(row_after_generate["total_collected"]) == Decimal("100000.00")

        await _regenerate_settlement_async(
            settlement_id, ctx["org_id"], "req-regen", Decimal("1200.0000"), ctx["user_id"]
        )

        row_after_regenerate = await seed.get_settlement_row(settlement_id)
        assert Decimal(row_after_regenerate["exchange_rate"]) == Decimal("1200.0000")
        assert Decimal(row_after_regenerate["total_collected"]) == Decimal("120000.00")

    async def test_regenerate_generates_new_export_attachments(self, seed):
        """RF-03: los exports viejos no se borran (RN-L03) y se agrega
        una version nueva -- verificado por la cantidad de adjuntos."""
        ctx = await _Ctx(seed).build()
        rent_period = await seed.create_rent_period_row(
            organization_id=ctx["org_id"], contract_id=ctx["contract_id"], period="2026-06-01"
        )
        await seed.create_payment_row(
            organization_id=ctx["org_id"],
            rent_period_id=rent_period,
            created_by=ctx["user_id"],
            amount="50000.00",
        )

        settlement_id = await _create_placeholder_settlement(
            ctx["org_id"],
            ctx["landlord_id"],
            generated_by=ctx["user_id"],
            commission_pct=Decimal("10.00"),
        )
        await _generate_settlement_async(settlement_id, ctx["org_id"], "req-gen")
        attachments_after_generate = await seed.get_attachments(settlement_id)
        assert len(attachments_after_generate) == 2  # xlsx + pdf

        await _regenerate_settlement_async(
            settlement_id, ctx["org_id"], "req-regen", None, ctx["user_id"]
        )
        attachments_after_regenerate = await seed.get_attachments(settlement_id)
        assert len(attachments_after_regenerate) == 4
