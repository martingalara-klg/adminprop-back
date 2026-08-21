"""tests/integration/settlements/test_needs_regeneration.py -- issue #30.

SDD: docs/sdd/features/spec_module_04_cobranzas.md §RF-05 +
spec_module_05_liquidaciones.md §RF-03 parrafo 3. Implements: CA-05-06
(mitad: cableado del hook -- la recomputacion de totales via
`POST /regenerate` se prueba en test_documents_worker_regenerate.py).

Cablea `modules/payments/settlement_hook.py` (no-op del #23) contra un
cobro real anulado via `POST /payments/:id/void` (modulo `payments`, ya
en el mismo proceso de la app -- sin mocks: valida la integracion real
cross-modulo)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_session_factory, set_tenant_context
from adminprop.modules.settlements.job_status import set_job_status
from adminprop.modules.settlements.repository import SettlementRepository

pytestmark = pytest.mark.asyncio


async def _seed_issued_settlement_with_payment(seed):
    org = await seed.create_organization_with_system_roles()
    owner = await seed.add_member(
        organization_id=org["organization_id"], role_id=org["roles"]["owner"], role_name="owner"
    )
    landlord_id = await seed.create_landlord_row(organization_id=org["organization_id"])
    renter_id = await seed.create_renter_row(organization_id=org["organization_id"])
    property_id = await seed.create_property_row(
        organization_id=org["organization_id"], landlord_id=landlord_id
    )
    contract_id = await seed.create_contract_row(
        organization_id=org["organization_id"], property_id=property_id, renter_id=renter_id
    )
    rent_period_id = await seed.create_rent_period_row(
        organization_id=org["organization_id"],
        contract_id=contract_id,
        period="2026-06-01",
        amount_due="80000.00",
        paid_total="80000.00",
        status="paid",
    )
    payment_id = await seed.create_payment_row(
        organization_id=org["organization_id"],
        rent_period_id=rent_period_id,
        created_by=owner["id"],
        amount="80000.00",
    )

    # Liquidacion `issued` con una linea que referencia el cobro -- sin
    # pasar por el worker real (mismo criterio de fixtures livianas que
    # `tests/integration/workers/test_documents_worker.py`).
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, org["organization_id"])
        repo = SettlementRepository(session)
        settlement = await repo.create_placeholder(
            organization_id=org["organization_id"],
            landlord_id=landlord_id,
            period=date(2026, 6, 1),
            exchange_rate=None,
            commission_pct_used=Decimal("10.00"),
            generated_by=owner["id"],
        )
        await repo.apply_calculation(
            settlement.id,
            org["organization_id"],
            total_collected=Decimal("80000.00"),
            commission_total=Decimal("8000.00"),
            charges_total=Decimal("0.00"),
            repairs_total=Decimal("0.00"),
            already_settled_total=Decimal("0.00"),
            net_amount=Decimal("72000.00"),
            line_items=[
                {
                    "line_type": "rent_collected",
                    "property_id": property_id,
                    "source_entity_type": "payment",
                    "source_entity_id": payment_id,
                    "original_amount": Decimal("80000.00"),
                    "original_currency": "ARS",
                    "amount_ars": Decimal("80000.00"),
                    "description": None,
                }
            ],
            settled_work_order_ids=[],
        )
        settlement_row = await repo.issue(settlement.id, org["organization_id"])
        await session.commit()

    return org, owner, settlement_row.id, payment_id


class TestCa0506NeedsRegenerationHook:
    """CA-05-06: "anular un cobro de una liquidacion emitida la marca
    'requiere regeneracion'; visible asi en el listado"."""

    async def test_voiding_payment_of_issued_settlement_marks_needs_regeneration(
        self, client, seed
    ):
        org, owner, settlement_id, payment_id = await _seed_issued_settlement_with_payment(seed)

        void_response = await client.post(
            f"/v1/payments/{payment_id}/void",
            json={"reason": "Cheque rechazado"},
            headers=owner["headers"],
        )
        assert void_response.status_code == 200

        list_response = await client.get("/v1/settlements", headers=owner["headers"])
        assert list_response.status_code == 200
        row = next(r for r in list_response.json()["data"] if r["id"] == str(settlement_id))
        assert row["needs_regeneration"] is True

        detail_response = await client.get(
            f"/v1/settlements/{settlement_id}", headers=owner["headers"]
        )
        assert detail_response.json()["data"]["needs_regeneration"] is True

        rows = await seed.audit_rows(org["organization_id"], "settlement.needs_regeneration")
        assert len(rows) == 1
        assert str(rows[0]["entity_id"]) == str(settlement_id)
        assert rows[0]["after_state"]["payment_id"] == str(payment_id)

    async def test_regeneration_clears_the_needs_regeneration_flag(self, client, seed, monkeypatch):
        """La bandera se deriva comparando el evento de auditoria contra
        `settlements.updated_at` -- una regeneracion posterior "limpia" la
        bandera sin necesidad de un UPDATE explicito (ver docstring de
        `list_needs_regeneration_flags`)."""
        monkeypatch.setattr(
            "adminprop.workers.documents_worker.regenerate_settlement.apply_async", MagicMock()
        )
        org, owner, settlement_id, payment_id = await _seed_issued_settlement_with_payment(seed)

        await client.post(
            f"/v1/payments/{payment_id}/void",
            json={"reason": "Cheque rechazado"},
            headers=owner["headers"],
        )

        await set_job_status(settlement_id, "completed")
        regenerate_response = await client.post(
            f"/v1/settlements/{settlement_id}/regenerate", json={}, headers=owner["headers"]
        )
        assert regenerate_response.status_code == 202

        # `apply_async` esta mockeado (no corre el recalculo real). Para
        # validar la bandera end-to-end sin correr el worker completo, se
        # fuerza el `updated_at` de la fila -- equivalente al efecto de
        # una regeneracion real ya terminada (`apply_regeneration` la
        # actualiza siempre).
        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await set_tenant_context(session, org["organization_id"])
            await session.execute(
                sa.text("UPDATE settlements SET updated_at = now() WHERE id = :id"),
                {"id": str(settlement_id)},
            )

        list_response = await client.get("/v1/settlements", headers=owner["headers"])
        row = next(r for r in list_response.json()["data"] if r["id"] == str(settlement_id))
        assert row["needs_regeneration"] is False

    async def test_voiding_payment_of_draft_settlement_does_not_flag_it(self, client, seed):
        """RF-03: solo liquidaciones `issued` necesitan la senal -- una
        `draft` se regenera libremente sin haber sido entregada."""
        org, owner, settlement_id, payment_id = await _seed_issued_settlement_with_payment(seed)
        # Revertir a `draft` para simular que nunca se emitio.
        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await set_tenant_context(session, org["organization_id"])
            await session.execute(
                sa.text("UPDATE settlements SET status = 'draft' WHERE id = :id"),
                {"id": str(settlement_id)},
            )

        await client.post(
            f"/v1/payments/{payment_id}/void",
            json={"reason": "Cheque rechazado"},
            headers=owner["headers"],
        )

        rows = await seed.audit_rows(org["organization_id"], "settlement.needs_regeneration")
        assert rows == []
