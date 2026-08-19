"""Logica de negocio de cobranzas: generacion mensual (issue #21) +
registro de cobros con mora sugerida y perdon (issue #22).

SDD: docs/sdd/features/spec_module_04_cobranzas.md §RF-01/RF-03/RF-04.
Implements: CA-04-01 (idempotencia), CA-04-02 (RN-P01 -- ajuste pending
bloquea la generacion del periodo), CA-04-03 (RN-P06, TC obligatorio),
CA-04-04 (RN-P05, parciales -- interes sobre el saldo restante), CA-04-05
(RN-P02/P03/P04, mora sugerida con dia de gracia + imputacion libre),
CA-04-06 (RN-D03, perdon auditado).

`RentPeriodService.generate_monthly` es el cuerpo de negocio del job
Beat `generate_rent_periods` (`sdd_04` §1.3), invocado por
`workers/notification_worker.py` una vez por organizacion `active` --
mismo patron que `ContractAdjustmentService.detect_due_adjustments` y
`ContractService.detect_expiring_and_expired` (issues #18/#19): recibe
una sesion ya tenant-scoped (`tenant_scoped_session`, con
`session.begin()` manejando el commit/rollback), y no llama a
`session.commit()` el mismo -- eso lo maneja el `async with` del caller.

`PaymentService` (issue #22) es distinta: es consumida por el router
HTTP (no por un worker), asi que SI maneja su propio `commit()` -- mismo
criterio que `ContractService`/`ContractAdjustmentService`.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from fastapi import Depends

from adminprop.modules.administracion.repository import (
    AdministracionRepository,
    get_administracion_repository,
)
from adminprop.modules.contracts.models import Contract
from adminprop.modules.contracts.rent_period_hook import (
    contract_has_pending_adjustment_for_period,
)
from adminprop.modules.contracts.repository import ContractRepository, get_contract_repository
from adminprop.modules.payments.models import Payment, RentPeriod
from adminprop.modules.payments.repository import (
    PaymentRepository,
    RentPeriodRepository,
    get_payment_repository,
    get_rent_period_repository,
)
from adminprop.shared.audit.service import audit
from adminprop.shared.errors.codes import (
    ExchangeRateRequiredException,
    NotFoundException,
    PaymentExceedsContractBalanceException,
    RentPeriodAlreadyPaidException,
)

# spec_module_07_administracion.md / provisioning.py.DEFAULT_ORGANIZATION_SETTINGS:
# piso de seguridad si `organizations.settings` no trae `grace_day` todavia
# (defensivo -- toda organizacion nueva la trae desde el provisioning).
_DEFAULT_GRACE_DAY = 10


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


class PaymentService:
    """RF-03 (registro de cobro) + RF-04 (mora sugerida y perdon)."""

    def __init__(
        self,
        repo: RentPeriodRepository,
        payment_repo: PaymentRepository,
        contract_repo: ContractRepository,
        admin_repo: AdministracionRepository,
    ) -> None:
        self._repo = repo
        self._payment_repo = payment_repo
        self._contract_repo = contract_repo
        self._admin_repo = admin_repo

    async def _load_rent_period_and_contract(
        self, rent_period_id: UUID, organization_id: UUID
    ) -> tuple[RentPeriod, Contract] | tuple[None, None]:
        """RN-D01: 404 unico para "no existe" y "es de otro tenant", tanto
        para el `rent_period` como para el `contract` que referencia (el
        contrato pertenece siempre al mismo tenant que su periodo, pero se
        revalida por defense in depth -- mismo criterio de
        `ContractRepository.property_exists`)."""
        rent_period = await self._repo.get_by_id(rent_period_id, organization_id)
        if rent_period is None:
            return None, None
        contract = await self._contract_repo.get_by_id(rent_period.contract_id, organization_id)
        if contract is None:  # pragma: no cover -- defensivo, integridad referencial de la DB
            return None, None
        return rent_period, contract

    async def _grace_day(self, organization_id: UUID) -> int:
        # RF-04: "dia de gracia de la org (default 10)" -- Modulo 7
        # (administracion) es la fuente de verdad de `grace_day`.
        settings = await self._admin_repo.get_organization_settings(organization_id)
        if settings is None:  # pragma: no cover -- defensivo, la org del JWT siempre existe
            return _DEFAULT_GRACE_DAY
        return int(settings.get("grace_day", _DEFAULT_GRACE_DAY))

    @staticmethod
    def _days_late(period: date, payment_date: date, grace_day: int) -> int:
        # RN-P02: "en termino hasta el dia de gracia inclusive; la mora
        # corre desde el dia siguiente (dia 11 = 1 dia de mora)" --
        # `due_date` es el dia de gracia del MES del periodo (`period` ya
        # esta normalizado al dia 1 del mes por la migracion #20).
        due_date = date(period.year, period.month, grace_day)
        return max((payment_date - due_date).days, 0)

    @staticmethod
    def _suggested_interest(
        balance: Decimal, daily_late_fee_pct: Decimal, days_late: int
    ) -> Decimal:
        # RN-P03: "interes sugerido = saldo impago x % de mora diaria del
        # contrato x dias de mora". `daily_late_fee_pct` es un porcentaje
        # (mismo criterio que `pct` de ajustes, `adjustment_service.py`:
        # se divide por 100 antes de aplicar).
        if days_late <= 0 or balance <= 0:
            return Decimal("0.00")
        interest = balance * (daily_late_fee_pct / Decimal(100)) * Decimal(days_late)
        return interest.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    async def preview_interest(
        self, rent_period_id: UUID, organization_id: UUID, payment_date: date
    ) -> dict:
        """RF-04: `GET /rent-periods/:id/interest-preview?payment_date=`."""
        rent_period, contract = await self._load_rent_period_and_contract(
            rent_period_id, organization_id
        )
        if rent_period is None:
            raise NotFoundException()

        balance = rent_period.amount_due - rent_period.paid_total
        grace_day = await self._grace_day(organization_id)
        days_late = self._days_late(rent_period.period, payment_date, grace_day)
        suggested_interest = self._suggested_interest(
            balance, contract.daily_late_fee_pct, days_late
        )
        return {
            "rent_period_id": rent_period.id,
            "payment_date": payment_date,
            "balance": balance,
            "days_late": days_late,
            "suggested_interest": suggested_interest,
        }

    async def register_payment(
        self,
        rent_period_id: UUID,
        organization_id: UUID,
        *,
        payment_date: date,
        method: str,
        payment_currency: str,
        amount: Decimal,
        exchange_rate: Decimal | None,
        destination: str,
        charged_interest: Decimal,
        notes: str | None,
        actor_user_id: UUID,
    ) -> Payment:
        """RF-03: registra el cobro -- RN-P05 (parciales), RN-P06 (TC),
        RN-P07 (destino, persistido tal cual -- el efecto en liquidaciones
        es Modulo 5) y RN-P04 (imputacion libre con perdon auditado,
        CA-04-06)."""
        rent_period, contract = await self._load_rent_period_and_contract(
            rent_period_id, organization_id
        )
        if rent_period is None:
            raise NotFoundException()

        # RF-03: "el periodo pasa a `paid` cuando el capital imputado
        # alcanza `amount_due`" -- ya no admite mas imputaciones.
        if rent_period.status == "paid":
            raise RentPeriodAlreadyPaidException()

        balance = rent_period.amount_due - rent_period.paid_total
        # RN-P05, CA-04-04: "importe > 0 y <= saldo" (`> 0` ya lo exige el
        # schema con `gt=0`).
        if amount > balance:
            raise PaymentExceedsContractBalanceException(
                field="amount",
                details={
                    "balance": str(balance),
                    "requested_amount": str(amount),
                    "rent_period_id": str(rent_period_id),
                },
            )

        # RN-P06, CA-04-03: TC obligatorio si la moneda del pago difiere
        # de la del contrato.
        if payment_currency != contract.currency and exchange_rate is None:
            raise ExchangeRateRequiredException(
                field="exchange_rate",
                details={
                    "contract_currency": contract.currency,
                    "payment_currency": payment_currency,
                },
            )

        grace_day = await self._grace_day(organization_id)
        days_late = self._days_late(rent_period.period, payment_date, grace_day)
        suggested_interest = self._suggested_interest(
            balance, contract.daily_late_fee_pct, days_late
        )
        # RN-P04: sugerido/cobrado/perdonado siempre quedan registrados.
        # "el sistema no impone tope" (Validaciones) -- si el operador
        # cobra mas que el sugerido, no hay nada que perdonar (perdonado
        # nunca es negativo).
        forgiven_interest = max(suggested_interest - charged_interest, Decimal("0.00"))

        payment = await self._payment_repo.insert(
            organization_id=organization_id,
            rent_period_id=rent_period.id,
            payment_date=payment_date,
            method=method,
            payment_currency=payment_currency,
            amount=amount,
            exchange_rate=exchange_rate,
            destination=destination,
            suggested_interest=suggested_interest,
            charged_interest=charged_interest,
            forgiven_interest=forgiven_interest,
            days_late=days_late,
            notes=notes,
            created_by=actor_user_id,
        )

        # RF-03: "el periodo pasa a `partial`... paid cuando el capital
        # imputado alcanza `amount_due`" (RN-P05, CA-04-04/05).
        new_paid_total = rent_period.paid_total + amount
        new_status = "paid" if new_paid_total >= rent_period.amount_due else "partial"
        await self._repo.update_after_payment(
            rent_period.id, organization_id, paid_total=new_paid_total, status=new_status
        )

        if forgiven_interest > 0:
            # CA-04-06: "todo perdon de interes queda en el log de
            # auditoria con autor y cobro asociado" -- misma transaccion
            # que el INSERT/UPDATE de arriba (confirmados juntos por el
            # `commit()` de abajo).
            await audit(
                self._payment_repo.session,
                organization_id=organization_id,
                action="interest.forgiven",
                entity_type="payment",
                entity_id=payment.id,
                before={"suggested_interest": str(suggested_interest)},
                after={
                    "charged_interest": str(charged_interest),
                    "forgiven_interest": str(forgiven_interest),
                },
                user_id=actor_user_id,
            )

        await self._payment_repo.commit()
        return payment


def get_payment_service(
    repo: RentPeriodRepository = Depends(get_rent_period_repository),
    payment_repo: PaymentRepository = Depends(get_payment_repository),
    contract_repo: ContractRepository = Depends(get_contract_repository),
    admin_repo: AdministracionRepository = Depends(get_administracion_repository),
) -> PaymentService:
    return PaymentService(repo, payment_repo, contract_repo, admin_repo)
