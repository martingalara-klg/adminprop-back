"""Logica de negocio de la generacion mensual de `rent_periods` (issue #21).

SDD: docs/sdd/features/spec_module_04_cobranzas.md §RF-01.
Implements: CA-04-01 (idempotencia), CA-04-02 (RN-P01 -- ajuste pending
bloquea la generacion del periodo).

`RentPeriodService.generate_monthly` es el cuerpo de negocio del job
Beat `generate_rent_periods` (`sdd_04` §1.3), invocado por
`workers/notification_worker.py` una vez por organizacion `active` --
mismo patron que `ContractAdjustmentService.detect_due_adjustments` y
`ContractService.detect_expiring_and_expired` (issues #18/#19): recibe
una sesion ya tenant-scoped (`tenant_scoped_session`, con
`session.begin()` manejando el commit/rollback), y no llama a
`session.commit()` el mismo -- eso lo maneja el `async with` del caller.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from adminprop.modules.contracts.rent_period_hook import (
    contract_has_pending_adjustment_for_period,
)
from adminprop.modules.contracts.repository import ContractRepository
from adminprop.modules.payments.repository import RentPeriodRepository


class RentPeriodService:
    def __init__(self, repo: RentPeriodRepository, contract_repo: ContractRepository) -> None:
        self._repo = repo
        self._contract_repo = contract_repo

    async def generate_monthly(self, *, organization_id: UUID, today: date) -> int:
        """RF-01: por cada contrato `active` de la organizacion, genera su
        `rent_period` del mes en curso con el monto vigente (`current_amount`
        + `currency`), salvo que el contrato tenga un ajuste `pending`
        para ese mismo periodo (RN-P01) -- en ese caso se salta hasta que
        el ajuste se aplique, momento en el que
        `rent_period_hook.maybe_generate_rent_period_for_adjustment` genera
        el periodo con el monto nuevo (issue #18).

        Idempotente (CA-04-01): `RentPeriodRepository.insert_pending` usa
        `ON CONFLICT DO NOTHING` sobre `(contract_id, period)` -- re-correr
        el job el mismo mes no duplica ningun periodo. `RN-07/RN-C05`:
        contratos `expired`/`terminated` no son candidatos --
        `ContractRepository.list_active` ya filtra `status = 'active'`.

        Devuelve la cantidad de `rent_periods` creados (informativo, para
        el log del worker).
        """
        period = date(today.year, today.month, 1)
        contracts = await self._contract_repo.list_active(organization_id)

        created = 0
        for contract in contracts:
            # RN-P01/CA-04-02: mientras haya un ajuste `pending` para este
            # periodo, el rent_period no se genera.
            if await contract_has_pending_adjustment_for_period(
                self._repo.session,
                contract_id=contract.id,
                organization_id=organization_id,
                period=period,
            ):
                continue

            new_id = await self._repo.insert_pending(
                organization_id=organization_id,
                contract_id=contract.id,
                period=period,
                amount_due=contract.current_amount,
                currency=contract.currency,
            )
            if new_id is not None:
                created += 1

        return created
