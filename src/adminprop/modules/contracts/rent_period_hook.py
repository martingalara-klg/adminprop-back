"""Hook de generacion de `rent_period` (issues #17, #18, #21).

SDD: spec_module_03_contratos.md §RF-03 ("`draft -> active`... genera el
rent_period del mes en curso si la fecha de inicio ya paso y aun no
existe") + §RF-04 paso 4 ("el rent_period del mes de ajuste no se genera
hasta que el ajuste este aplicado -- RN-P01; una vez aplicado, se genera
con el monto nuevo").

Issue #21: la tabla `rent_periods` ya existe (issue #20) y su capa ORM
vive en `modules/payments/models.py`/`repository.py` (issue #21) -- estos
dos hooks reemplazan el no-op que dejaron los issues #17/#18 por el
INSERT real (`ON CONFLICT DO NOTHING` sobre `(contract_id, period)`,
sdd_04 §1.3), reutilizando `RentPeriodRepository.insert_pending`.
`contract_has_pending_adjustment_for_period` no cambia: ya operaba sobre
`contract_adjustments` (que existe desde el issue #16) y es la misma
funcion que ahora tambien consulta el job mensual `generate_rent_periods`
(`modules/payments/service.py.RentPeriodService.generate_monthly`).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from adminprop.modules.contracts.models import ContractAdjustment
from adminprop.modules.payments.repository import RentPeriodRepository


async def maybe_generate_current_month_rent_period(
    session: AsyncSession,
    *,
    contract_id: UUID,
    organization_id: UUID,
    start_date: date,
    today: date,
    amount_due: Decimal,
    currency: str,
) -> UUID | None:
    """RF-03 (CA-01-04/CA-03-01): al activar un contrato, si su
    `start_date` ya paso, genera en el acto el `rent_period` del mes en
    curso con el monto vigente del contrato (`amount_due`/`currency` los
    resuelve el caller -- `ContractService.activate` -- desde la fila
    recien actualizada). RN-P01/CA-04-02: si el contrato tiene un ajuste
    `pending` para ese mismo mes, el periodo NO se genera todavia (nacera
    cuando se aplique el ajuste, ver `maybe_generate_rent_period_for_adjustment`).
    Idempotente via `RentPeriodRepository.insert_pending`.
    """
    if start_date > today:
        return None

    period = date(today.year, today.month, 1)
    if await contract_has_pending_adjustment_for_period(
        session, contract_id=contract_id, organization_id=organization_id, period=period
    ):
        return None

    return await RentPeriodRepository(session).insert_pending(
        organization_id=organization_id,
        contract_id=contract_id,
        period=period,
        amount_due=amount_due,
        currency=currency,
    )


async def contract_has_pending_adjustment_for_period(
    session: AsyncSession,
    *,
    contract_id: UUID,
    organization_id: UUID,
    period: date,
) -> bool:
    """RN-P01 (spec_module_03_contratos.md §RF-04 paso 4, sdd_02 §2.8): el
    `rent_period` de `period` no debe generarse mientras el contrato tenga
    un ajuste `pending` con ese mismo `due_period`. Reutilizable por
    `adjustment_service` (para decidir si dispara la generacion del mes
    recien aplicado), por `maybe_generate_current_month_rent_period` (esta
    misma tabla, RF-03) y por el job mensual `generate_rent_periods`
    (issue #21, `modules/payments/service.py`) antes de insertar cada
    periodo."""
    stmt = select(ContractAdjustment.id).where(
        ContractAdjustment.contract_id == contract_id,
        ContractAdjustment.organization_id == organization_id,
        ContractAdjustment.due_period == period,
        ContractAdjustment.status == "pending",
    )
    result = await session.execute(stmt)
    return result.first() is not None


async def maybe_generate_rent_period_for_adjustment(
    session: AsyncSession,
    *,
    contract_id: UUID,
    organization_id: UUID,
    period: date,
    amount_due: Decimal,
    currency: str,
) -> UUID | None:
    """RF-04 paso 4 (CA-04-02): llamado desde
    `adjustment_service.ContractAdjustmentService.apply` inmediatamente
    despues de marcar el ajuste `applied` -- "una vez aplicado, se genera
    con el monto nuevo". No vuelve a chequear `contract_has_pending_adjustment_for_period`:
    el ajuste que se acaba de aplicar YA paso a `applied` en la misma
    transaccion, asi que el guard de RN-P01 ya no bloquea este periodo.
    Idempotente via `RentPeriodRepository.insert_pending` (si el periodo
    ya existiera por otra via, no lo duplica ni lo pisa)."""
    return await RentPeriodRepository(session).insert_pending(
        organization_id=organization_id,
        contract_id=contract_id,
        period=period,
        amount_due=amount_due,
        currency=currency,
    )
