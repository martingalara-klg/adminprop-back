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
from adminprop.modules.contracts.monthly_amounts import MonthlyAmountRow
from adminprop.modules.payments.repository import (
    PaymentRepository,
    RentPeriodRepository,
)


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


# RN-P09 (issue #119): "Cobro registrado automaticamente al dar de alta
# el contrato en curso." -- texto LITERAL del issue, no reescribir.
_INITIAL_LOAD_NOTES = "Cobro registrado automáticamente al dar de alta el contrato en curso."


async def generate_initial_load_history(
    session: AsyncSession,
    *,
    contract_id: UUID,
    organization_id: UUID,
    currency: str,
    past_periods: list[MonthlyAmountRow],
    actor_user_id: UUID,
) -> int:
    """RN-11/RN-P09 (issue #119, feedback #3 del PO): al dar de alta un
    contrato en curso (`start_date` anterior al mes actual), genera un
    `RentPeriod` `paid` + un `Payment` `origin='initial_load'` por cada
    mes YA TRANSCURRIDO (nunca el mes actual -- el caller,
    `ContractService.create`, ya filtro `past_periods` para excluirlo).
    `amount_due`/monto del cobro = el monto vigente de CADA mes segun
    `monthly_amounts.compute_monthly_amounts` (coherente con
    `historical_amounts[]`/RN-08 cuando el contrato declara tramos; si no
    hay tramos -- ej. arranco el mes pasado sin ajuste -- es
    `initial_amount` plano).

    Cobro sintetico: moneda del contrato, sin TC (`exchange_rate=None`),
    interes 0 (`suggested_interest`/`charged_interest`/`forgiven_interest`
    = 0, `days_late=0`), `payment_date` = dia 1 del periodo (no hay una
    fecha real de cobro que preservar -- es un registro retroactivo,
    decision de implementacion), `destination='landlord_account'` (el
    dinero ya lo cobro el propietario directamente, por fuera del sistema,
    antes del alta -- distinto del CHECK de exclusion de liquidaciones,
    que es `origin`, no `destination`: ver `settlements/repository.py.
    _PAYMENTS_SQL`).

    Llamado en la MISMA transaccion que el INSERT del contrato (el
    caller hace el `commit()` final) -- si algo falla, nada de esto queda
    a medias. Idempotente via `RentPeriodRepository.insert_paid` (si el
    periodo ya existiera -- no deberia pasar para un contrato recien
    creado -- no duplica el `Payment` asociado). Devuelve la cantidad de
    pares periodo+cobro generados (para el evento de auditoria resumen,
    ver `ContractService.create`)."""
    rent_period_repo = RentPeriodRepository(session)
    payment_repo = PaymentRepository(session)

    created = 0
    for row in past_periods:
        rent_period_id = await rent_period_repo.insert_paid(
            organization_id=organization_id,
            contract_id=contract_id,
            period=row.period,
            amount_due=row.amount,
            currency=currency,
        )
        if rent_period_id is None:  # pragma: no cover -- defensivo, ver docstring
            continue

        await payment_repo.insert(
            organization_id=organization_id,
            rent_period_id=rent_period_id,
            payment_date=row.period,
            method="transfer",
            payment_currency=currency,
            amount=row.amount,
            exchange_rate=None,
            destination="landlord_account",
            suggested_interest=Decimal("0.00"),
            charged_interest=Decimal("0.00"),
            forgiven_interest=Decimal("0.00"),
            days_late=0,
            notes=_INITIAL_LOAD_NOTES,
            created_by=actor_user_id,
            origin="initial_load",
        )
        created += 1

    return created
