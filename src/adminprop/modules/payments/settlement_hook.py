"""RF-05 (issue #23) + RF-03/CA-05-06 (issue #30): "un cobro incluido en
una liquidacion emitida puede anularse igual: la liquidacion afectada
queda marcada para regeneracion" (spec_module_04_cobranzas.md §RF-05,
spec_module_05_liquidaciones.md §RF-03 parrafo 3).

Modulo 5 (Liquidaciones) ya existe (issue #29) -- este hook deja de ser
no-op: busca las liquidaciones `issued` que incluyen `payment_id` en su
detalle (`settlement_line_items.source_entity_type='payment'`) y las
marca "requiere regeneracion" con un evento de auditoria
`settlement.needs_regeneration` (RN-D04, correccion de cobros/liquidaciones
siempre trazada). No hay columna `needs_regeneration` en `settlements` (la
migracion #27 es fiel al spec, que no la declara, y este issue no agrega
migraciones) -- la bandera se DERIVA comparando el ultimo evento de este
tipo contra `settlements.updated_at`
(`SettlementRepository.list_needs_regeneration_flags`), asi que este hook
solo necesita insertar el evento, nunca "limpiarlo": una regeneracion
posterior actualiza `updated_at` y la bandera desaparece sola.

Solo liquidaciones `issued` necesitan esta senal: una `draft` se
regenera libremente sin haber sido entregada todavia (RF-03) --
`find_issued_settlement_ids_by_payment` ya filtra por `status='issued'`.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from adminprop.modules.settlements.repository import SettlementRepository
from adminprop.shared.audit.service import audit


async def maybe_mark_settlements_for_regeneration(
    session: AsyncSession,
    *,
    organization_id: UUID,
    payment_id: UUID,
) -> None:
    """CA-05-06: anular un cobro de una liquidacion `issued` la marca
    "requiere regeneracion" (visible en `GET /settlements`, ver
    `service.py.list`). Misma transaccion que el UPDATE/anulacion del
    cobro (el caller, `PaymentService.void_payment`, hace el `commit()`
    despues de invocar este hook) -- si esa transaccion hace rollback, el
    evento de auditoria tambien."""
    settlement_repo = SettlementRepository(session)
    settlement_ids = await settlement_repo.find_issued_settlement_ids_by_payment(
        payment_id, organization_id
    )
    for settlement_id in settlement_ids:
        await audit(
            session,
            organization_id=organization_id,
            action="settlement.needs_regeneration",
            entity_type="settlement",
            entity_id=settlement_id,
            after={"reason": "payment_voided", "payment_id": str(payment_id)},
        )
