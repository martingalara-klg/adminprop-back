"""Punto de extension para RF-05 (issue #23): "un cobro incluido en una
liquidacion emitida puede anularse igual: la liquidacion afectada queda
marcada para regeneracion (Modulo 5 RF-03)"
(spec_module_04_cobranzas.md §RF-05).

El modulo de Liquidaciones (Modulo 5) no existe todavia -- Fase 7, issue
#29. Este hook es deliberadamente no-op (mismo patron que
`modules/contracts/rent_period_hook.py` documenta para los issues #17/#18
antes de que `rent_periods` existiera): deja la firma final lista para
que `PaymentService.void_payment` la invoque sin cambios cuando el modulo
de liquidaciones exista -- en ese momento, reemplazar el cuerpo por la
busqueda de la/las `settlement_line` que referencian `payment_id` y su
marcado como `needs_regeneration` (RF-03 de Modulo 5).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


async def maybe_mark_settlements_for_regeneration(
    session: AsyncSession,
    *,
    organization_id: UUID,
    payment_id: UUID,
) -> None:
    """No-op (issue #23): Modulo 5 (Liquidaciones, issue #29) todavia no
    existe -- no hay `settlement_lines` que marcar. Cuando exista,
    reemplazar por el UPDATE real de las liquidaciones emitidas que
    incluyen este `payment_id`. Parametros sin usar hoy a proposito -- la
    firma queda lista para ese reemplazo (mismo patron que
    `modules/contracts/rent_period_hook.py` documenta para los issues
    #17/#18)."""
    return
