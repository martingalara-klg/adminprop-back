"""Punto de extension para RF-05/CA-06-07 (issue #26): "Un pedido closed
ya liquidado no puede cancelarse ni reabrirse"
(spec_module_06_mantenimiento.md §RF-05, RN-04).

`work_orders.settled_in_settlement_id` (Capa 6, issue #27) todavia no
existe -- mismo patron que `modules/payments/settlement_hook.py` documenta
para Modulo 5: la firma final queda lista para que
`WorkOrderService.cancel` la invoque sin cambios cuando la columna
exista (issue #27 agrega la columna + reemplaza el cuerpo de esta
funcion por `work_order.settled_in_settlement_id is not None`).

CONCERN documentado en el PR (ver reporte de la sesion): hasta que exista
esa columna, la UNICA senal disponible hoy para aproximar "ya liquidado"
es `status == 'closed'` -- esto es mas estricto que el spec final (un
pedido `closed` con `payer='landlord'` nunca se liquida via el modulo de
liquidaciones, pero igual queda bloqueado para cancelar/reabrir bajo esta
aproximacion). Se documenta como limitacion conocida en vez de dejar
`cancel()` sin ninguna proteccion para pedidos cerrados.
"""

from __future__ import annotations

from adminprop.modules.maintenance.models import WorkOrder


def is_work_order_settled(work_order: WorkOrder) -> bool:
    """Aproximacion documentada (ver docstring del modulo): hasta que
    exista `settled_in_settlement_id` (issue #27), todo pedido `closed`
    se trata como si ya estuviera liquidado a efectos de CA-06-07."""
    return work_order.status == "closed"
