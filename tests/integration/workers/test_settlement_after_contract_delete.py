"""tests/integration/workers/test_settlement_after_contract_delete.py

SDD: docs/sdd/features/spec_module_03_contratos.md v1.7 RF-07 (RN-13 =
RN-C08, issue #124, decision #130) + spec_module_05_liquidaciones.md.
Implements: CA-03-40 (parte liquidaciones: una liquidacion ya generada
que incluye cobros del contrato eliminado queda integra tras la
eliminacion -- totales y line items sin cambios).

Mismo criterio que tests/integration/workers/test_documents_worker.py:
se invoca `_generate_settlement_async` directamente contra Postgres (el
wrapper Celery sincronico no puede llamarse desde el loop de
pytest-asyncio).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from adminprop.db.session import get_session_factory, set_tenant_context
from adminprop.modules.contracts.repository import ContractRepository
from adminprop.modules.contracts.service import ContractService
from adminprop.modules.properties.repository import PropertyRepository
from adminprop.modules.settlements.repository import SettlementRepository
from adminprop.workers.documents_worker import _generate_settlement_async

pytestmark = pytest.mark.asyncio


async def _create_placeholder_settlement(
    org_id: uuid.UUID,
    landlord_id: uuid.UUID,
    *,
    generated_by: uuid.UUID,
    commission_pct: Decimal,
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
            exchange_rate=None,
            commission_pct_used=commission_pct,
            generated_by=generated_by,
        )
        return settlement.id


async def _delete_contract(org_id: uuid.UUID, contract_id: uuid.UUID, actor: uuid.UUID) -> None:
    """RN-C08: borrado logico real via `ContractService.delete` (mismo
    codigo que ejecuta `DELETE /contracts/:id`), no un UPDATE manual."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        await set_tenant_context(session, org_id)
        service = ContractService(ContractRepository(session), PropertyRepository(session))
        await service.delete(contract_id, org_id, actor_user_id=actor)


class TestCa0340SettlementIntactAfterContractDelete:
    """CA-03-40: una liquidacion ya emitida que incluye cobros del
    contrato eliminado queda integra tras la eliminacion (totales y line
    items sin cambios)."""

    async def test_ca_03_40_settlement_totals_and_line_items_unchanged(self, seed):
        org_id = await seed.create_organization()
        user = await seed.create_user()
        landlord_id = await seed.create_landlord_row(organization_id=org_id, commission_pct="10.00")
        renter_id = await seed.create_renter_row(organization_id=org_id)
        property_id = await seed.create_property_row(
            organization_id=org_id, landlord_id=landlord_id, address="Prop liquidada"
        )
        contract_id = await seed.create_contract_row(
            organization_id=org_id, property_id=property_id, renter_id=renter_id
        )
        rent_period_id = await seed.create_rent_period_row(
            organization_id=org_id,
            contract_id=contract_id,
            period="2026-06-01",
            status="paid",
        )
        await seed.create_payment_row(
            organization_id=org_id,
            rent_period_id=rent_period_id,
            created_by=user["id"],
            amount="100000.00",
        )
        settlement_id = await _create_placeholder_settlement(
            org_id,
            landlord_id,
            generated_by=user["id"],
            commission_pct=Decimal("10.00"),
        )
        await _generate_settlement_async(settlement_id, org_id, "req-ca-03-40")

        row_before = await seed.get_settlement_row(settlement_id)
        items_before = await seed.get_line_items(settlement_id)
        assert items_before, "la liquidacion debe tener line items del cobro"

        await _delete_contract(org_id, contract_id, user["id"])

        # CA-03-40: la liquidacion emitida queda INTACTA -- ni totales ni
        # line items cambian por la eliminacion del contrato.
        row_after = await seed.get_settlement_row(settlement_id)
        items_after = await seed.get_line_items(settlement_id)
        assert row_after == row_before
        assert items_after == items_before
