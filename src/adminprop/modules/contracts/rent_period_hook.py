"""Hook extensible de generacion de `rent_period` (issues #17 y #18).

SDD: spec_module_03_contratos.md §RF-03 ("`draft -> active`... genera el
rent_period del mes en curso si la fecha de inicio ya paso y aun no
existe") + §RF-04 paso 4 ("el rent_period del mes de ajuste no se genera
hasta que el ajuste este aplicado -- RN-P01; una vez aplicado, se genera
con el monto nuevo"). La tabla `rent_periods` NO existe todavia
(migracion prevista para el issue #20/#21, Modulo 4 Cobranzas) -- este
hook deja los puntos de extension declarados y llamados desde
`service.activate` y `adjustment_service.apply`, mismo criterio que
`modules/properties/repository.py.PropertyRepository.has_active_dependencies`
dejo para el contrato activo antes de que este modulo existiera.

Cuando `rent_periods` exista: reemplazar el cuerpo de
`maybe_generate_current_month_rent_period` y
`maybe_generate_rent_period_for_adjustment` por el INSERT real
(`ON CONFLICT DO NOTHING` sobre `(contract_id, period)` para idempotencia,
sdd_04 §1.3) -- las firmas ya quedan listas para ese reemplazo sin tocar
los callers. `contract_has_pending_adjustment_for_period` SI tiene efecto
hoy (opera sobre `contract_adjustments`, que ya existe) -- es la funcion
que el futuro job mensual `generate_rent_periods` (issue #21, RN-P01)
debe consultar antes de generar el `rent_period` de un contrato/periodo:
mientras haya un ajuste `pending` para ese `due_period`, el periodo NO se
genera.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from adminprop.modules.contracts.models import ContractAdjustment


async def maybe_generate_current_month_rent_period(
    *,
    contract_id: UUID,
    organization_id: UUID,
    start_date: date,
    today: date,
) -> None:
    """No-op hoy: `rent_periods` no existe (issue #20). Documentado en vez
    de omitido para que el llamador (`activate`) exprese la intencion del
    RF-03 aunque el efecto todavia no sea observable."""
    return


async def contract_has_pending_adjustment_for_period(
    session: AsyncSession,
    *,
    contract_id: UUID,
    organization_id: UUID,
    period: date,
) -> bool:
    """RN-P01 (spec_module_03_contratos.md §RF-04 paso 4, sdd_02 §2.8): el
    `rent_period` de `period` no debe generarse mientras el contrato tenga
    un ajuste `pending` con ese mismo `due_period`. Reutilizable hoy por
    `adjustment_service` (para decidir si dispara la generacion del mes
    recien aplicado) y, cuando exista, por el job mensual
    `generate_rent_periods` (issue #21) antes de insertar cada periodo."""
    stmt = select(ContractAdjustment.id).where(
        ContractAdjustment.contract_id == contract_id,
        ContractAdjustment.organization_id == organization_id,
        ContractAdjustment.due_period == period,
        ContractAdjustment.status == "pending",
    )
    result = await session.execute(stmt)
    return result.first() is not None


async def maybe_generate_rent_period_for_adjustment(
    *,
    contract_id: UUID,
    organization_id: UUID,
    period: date,
    amount_due,
) -> None:
    """No-op hoy: `rent_periods` no existe (issue #20/#21). Llamado desde
    `adjustment_service.ContractAdjustmentService.apply` inmediatamente
    despues de marcar el ajuste `applied` (RF-04 paso 4: "una vez
    aplicado, se genera con el monto nuevo") -- deja declarada la
    intencion del flujo aunque el efecto todavia no sea observable, mismo
    criterio que `maybe_generate_current_month_rent_period` (issue #17)."""
    return
