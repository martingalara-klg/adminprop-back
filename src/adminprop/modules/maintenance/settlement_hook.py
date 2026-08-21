"""Punto de extension para RF-05/CA-06-07 (issue #26): "Un pedido closed
ya liquidado no puede cancelarse ni reabrirse"
(spec_module_06_mantenimiento.md §RF-05, RN-04).

Issue #29 (Modulo 5, liquidaciones): `work_orders.settled_in_settlement_id`
ya esta mapeada en el modelo ORM (`modules/maintenance/models.py.WorkOrder`)
y `SettlementRepository.apply_calculation` la estampa cuando una
reparacion `closed`/`payer=agency` se incluye en una liquidacion (RN-L04).
Cierra el CONCERN que dejo el issue #26: la aproximacion `status ==
'closed'` (mas estricta que el spec real -- bloqueaba tambien pedidos
`payer='landlord'`, que nunca se liquidan via este modulo) queda
reemplazada por la senal real.
"""

from __future__ import annotations

from adminprop.modules.maintenance.models import WorkOrder


def is_work_order_settled(work_order: WorkOrder) -> bool:
    """RN-L04: un pedido queda "ya liquidado" cuando quedo vinculado a una
    liquidacion (`settled_in_settlement_id IS NOT NULL`) -- la senal real,
    ya no la aproximacion por `status == 'closed'` (issue #26 CONCERN,
    cerrado en el issue #29)."""
    return work_order.settled_in_settlement_id is not None
