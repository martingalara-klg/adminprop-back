"""Logica de negocio de cobranzas: generacion mensual (issue #21) +
registro de cobros con mora sugerida y perdon (issue #22) + panel del
mes, anulacion y estado de deuda (issue #23).

SDD: docs/sdd/features/spec_module_04_cobranzas.md §RF-01..RF-06.
Implements: CA-04-01 (idempotencia), CA-04-02 (RN-P01 -- ajuste pending
bloquea la generacion del periodo), CA-04-03 (RN-P06, TC obligatorio),
CA-04-04 (RN-P05, parciales -- interes sobre el saldo restante), CA-04-05
(RN-P02/P03/P04, mora sugerida con dia de gracia + imputacion libre),
CA-04-06 (RN-D03, perdon auditado), CA-04-07 (RN-D04, anulacion
auditada), CA-04-09/CA-02-05 (estado de deuda).

`RentPeriodService.generate_monthly` es el cuerpo de negocio del job
Beat `generate_rent_periods` (`sdd_04` §1.3), invocado por
`workers/notification_worker.py` una vez por organizacion `active` --
mismo patron que `ContractAdjustmentService.detect_due_adjustments` y
`ContractService.detect_expiring_and_expired` (issues #18/#19): recibe
una sesion ya tenant-scoped (`tenant_scoped_session`, con
`session.begin()` manejando el commit/rollback), y no llama a
`session.commit()` el mismo -- eso lo maneja el `async with` del caller.

`PaymentService` (issues #22/#23) y `DebtService` (issue #23) son
distintas: son consumidas por el router HTTP (no por un worker), asi que
SI manejan su propio `commit()` -- mismo criterio que
`ContractService`/`ContractAdjustmentService`.

`compute_days_late`/`compute_suggested_interest` (RN-P02/P03) se declaran
a nivel de modulo (no como metodos privados de `PaymentService`, como en
el issue #22) para que `DebtService` (panel del mes RF-02, deuda
RF-06/CA-02-05) las reutilice tal cual sin duplicar el calculo -- pedido
explicito del issue #23 ("REUTILIZALO... no lo dupliques")."""

from __future__ import annotations

import base64
from dataclasses import dataclass
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
    RentPeriodCandidate,
    RentPeriodRepository,
    get_payment_repository,
    get_rent_period_repository,
)
from adminprop.modules.payments.settlement_hook import maybe_mark_settlements_for_regeneration
from adminprop.shared.audit.service import audit
from adminprop.shared.errors.codes import (
    ExchangeRateRequiredException,
    NotFoundException,
    PaymentAlreadyVoidedException,
    PaymentExceedsContractBalanceException,
    RentPeriodAlreadyPaidException,
)

# spec_module_07_administracion.md / provisioning.py.DEFAULT_ORGANIZATION_SETTINGS:
# piso de seguridad si `organizations.settings` no trae `grace_day` todavia
# (defensivo -- toda organizacion nueva la trae desde el provisioning).
_DEFAULT_GRACE_DAY = 10

# RF-02: "en mora" es derivado -- pending/partial con el dia de gracia
# vencido (spec_data_model.md §Capa 4 "rent_periods.status": "'en mora'
# es derivado (fecha vs grace_day)").
_UNPAID_STATUSES: tuple[str, ...] = ("pending", "partial")


def compute_days_late(period: date, as_of: date, grace_day: int) -> int:
    """RN-P02: "en termino hasta el dia de gracia inclusive; la mora corre
    desde el dia siguiente (dia 11 = 1 dia de mora)" -- `due_date` es el
    dia de gracia del MES del periodo (`period` normalizado al dia 1 del
    mes). Reutilizada por `PaymentService` (interes al momento del cobro)
    y `DebtService` (interes acumulado al dia de hoy, RF-02/RF-06)."""
    due_date = date(period.year, period.month, grace_day)
    return max((as_of - due_date).days, 0)


def compute_suggested_interest(
    balance: Decimal, daily_late_fee_pct: Decimal, days_late: int
) -> Decimal:
    """RN-P03: "interes sugerido = saldo impago x % de mora diaria del
    contrato x dias de mora". `daily_late_fee_pct` es un porcentaje (se
    divide por 100 antes de aplicar, mismo criterio que `pct` de
    ajustes)."""
    if days_late <= 0 or balance <= 0:
        return Decimal("0.00")
    interest = balance * (daily_late_fee_pct / Decimal(100)) * Decimal(days_late)
    return interest.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


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
        days_late = compute_days_late(rent_period.period, payment_date, grace_day)
        suggested_interest = compute_suggested_interest(
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
        days_late = compute_days_late(rent_period.period, payment_date, grace_day)
        suggested_interest = compute_suggested_interest(
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

    async def void_payment(
        self,
        payment_id: UUID,
        organization_id: UUID,
        *,
        reason: str,
        actor_user_id: UUID,
    ) -> Payment:
        """RF-05/CA-04-07: anulacion logica del cobro -- recompone el
        saldo del periodo (`paid_total`/`status`), audita con autor y
        motivo (RN-D04), y deja el punto de extension de liquidaciones
        (RF-05 parrafo 2, Modulo 5 RF-03, issue #29) invocado (no-op hoy,
        ver `settlement_hook.py`). Segunda anulacion sobre el mismo cobro
        -> `409 PAYMENT_ALREADY_VOIDED`."""
        payment = await self._payment_repo.get_by_id(payment_id, organization_id)
        if payment is None:
            raise NotFoundException()
        if payment.voided_at is not None:
            raise PaymentAlreadyVoidedException()

        rent_period = await self._repo.get_by_id(payment.rent_period_id, organization_id)
        if rent_period is None:  # pragma: no cover -- defensivo, integridad referencial de la DB
            raise NotFoundException()

        # CA-04-07: "recompone el saldo del periodo (paid->partial o
        # partial->pending segun corresponda)" -- restar el capital del
        # cobro anulado nunca puede dejar `paid_total` negativo (RN-P05 ya
        # garantiza que ningun cobro superaba el saldo al momento de
        # registrarse), pero se acota a 0 por robustez ante datos
        # historicos.
        new_paid_total = max(rent_period.paid_total - payment.amount, Decimal("0.00"))
        new_status = "pending" if new_paid_total <= 0 else "partial"
        await self._repo.update_after_payment(
            rent_period.id, organization_id, paid_total=new_paid_total, status=new_status
        )

        voided_payment = await self._payment_repo.void(
            payment_id, organization_id, voided_by=actor_user_id
        )

        # CA-04-07: "la anulacion se audita con autor y motivo" -- misma
        # transaccion que el UPDATE de arriba (confirmados juntos por el
        # `commit()` de abajo).
        await audit(
            self._payment_repo.session,
            organization_id=organization_id,
            action="payment.voided",
            entity_type="payment",
            entity_id=payment_id,
            before={"paid_total": str(rent_period.paid_total), "status": rent_period.status},
            after={"reason": reason, "paid_total": str(new_paid_total), "status": new_status},
            user_id=actor_user_id,
        )

        # RF-05 parrafo 2 (Modulo 5 RF-03, issue #29): no-op hoy -- ver
        # docstring de `settlement_hook.py`.
        await maybe_mark_settlements_for_regeneration(
            self._payment_repo.session,
            organization_id=organization_id,
            payment_id=payment_id,
        )

        await self._payment_repo.commit()
        return voided_payment


def get_payment_service(
    repo: RentPeriodRepository = Depends(get_rent_period_repository),
    payment_repo: PaymentRepository = Depends(get_payment_repository),
    contract_repo: ContractRepository = Depends(get_contract_repository),
    admin_repo: AdministracionRepository = Depends(get_administracion_repository),
) -> PaymentService:
    return PaymentService(repo, payment_repo, contract_repo, admin_repo)


# ─── RF-02 (panel del mes) + RF-06/CA-02-05 (estado de deuda) — issue #23 ──


@dataclass(frozen=True)
class RentPeriodPanelEntry:
    """RF-02: fila del panel del mes -- `RentPeriodCandidate` + los
    campos calculados (`balance`, `days_late`, `suggested_interest`,
    `in_arrears`) que dependen de `today`/`grace_day` (RN-P02/P03)."""

    id: UUID
    contract_id: UUID
    property_id: UUID
    landlord_id: UUID
    renter_id: UUID
    period: date
    amount_due: Decimal
    currency: str
    status: str
    paid_total: Decimal
    balance: Decimal
    in_arrears: bool
    days_late: int
    suggested_interest: Decimal


@dataclass(frozen=True)
class DebtEntry:
    """RF-06/CA-02-05: deuda acumulada de un contrato (inquilino +
    propiedad) -- agregada sobre todos sus `rent_periods` `pending`/
    `partial`. `days_late` es el del periodo mas antiguo adeudado (el
    que primero entro en mora, "desde cuando" debe -- UC-10)."""

    contract_id: UUID
    property_id: UUID
    landlord_id: UUID
    renter_id: UUID
    periods_overdue: int
    balance: Decimal
    days_late: int
    suggested_interest: Decimal


def _to_panel_entry(
    candidate: RentPeriodCandidate, *, today: date, grace_day: int
) -> RentPeriodPanelEntry:
    balance = candidate.amount_due - candidate.paid_total
    days_late = compute_days_late(candidate.period, today, grace_day)
    suggested_interest = compute_suggested_interest(
        balance, candidate.daily_late_fee_pct, days_late
    )
    # RF-02: "en mora" (pendiente o parcial con el dia de gracia vencido).
    in_arrears = candidate.status in _UNPAID_STATUSES and days_late > 0
    return RentPeriodPanelEntry(
        id=candidate.id,
        contract_id=candidate.contract_id,
        property_id=candidate.property_id,
        landlord_id=candidate.landlord_id,
        renter_id=candidate.renter_id,
        period=candidate.period,
        amount_due=candidate.amount_due,
        currency=candidate.currency,
        status=candidate.status,
        paid_total=candidate.paid_total,
        balance=balance,
        in_arrears=in_arrears,
        days_late=days_late,
        suggested_interest=suggested_interest,
    )


class RentPeriodPanelService:
    """RF-02: panel de cobranzas del mes (`GET /rent-periods`,
    `GET /rent-periods/:id`)."""

    def __init__(self, repo: RentPeriodRepository, admin_repo: AdministracionRepository) -> None:
        self._repo = repo
        self._admin_repo = admin_repo

    async def _grace_day(self, organization_id: UUID) -> int:
        settings = await self._admin_repo.get_organization_settings(organization_id)
        if settings is None:  # pragma: no cover -- defensivo, la org del JWT siempre existe
            return _DEFAULT_GRACE_DAY
        return int(settings.get("grace_day", _DEFAULT_GRACE_DAY))

    async def list_panel(
        self,
        *,
        organization_id: UUID,
        today: date,
        period: date | None,
        status: str | None,
        in_arrears: bool | None,
        property_id: UUID | None,
        landlord_id: UUID | None,
        renter_id: UUID | None,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[RentPeriodPanelEntry], str | None]:
        """RF-02: `?period=YYYY-MM&status=&in_arrears=true`, filtros de
        propiedad/propietario/inquilino. `in_arrears` se resuelve en
        Python (depende de `today`/`grace_day`, no es una columna) --
        paginacion por cursor tambien en Python sobre la lista ya
        filtrada/ordenada (escala MVP: volumen mensual por organizacion,
        no requiere paginar a nivel SQL para este reporte)."""
        grace_day = await self._grace_day(organization_id)
        candidates = await self._repo.list_candidates(
            organization_id=organization_id,
            period=period,
            status=status,
            property_id=property_id,
            landlord_id=landlord_id,
            renter_id=renter_id,
        )
        entries = [_to_panel_entry(c, today=today, grace_day=grace_day) for c in candidates]
        if in_arrears is not None:
            entries = [e for e in entries if e.in_arrears == in_arrears]

        start = _decode_index_cursor(cursor)
        page = entries[start : start + limit]
        next_cursor = _encode_index_cursor(start + limit) if start + limit < len(entries) else None
        return page, next_cursor

    async def get_panel_entry(
        self, rent_period_id: UUID, organization_id: UUID, *, today: date
    ) -> RentPeriodPanelEntry | None:
        """RF-02: `GET /rent-periods/:id`."""
        candidate = await self._repo.get_candidate(rent_period_id, organization_id)
        if candidate is None:
            return None
        grace_day = await self._grace_day(organization_id)
        return _to_panel_entry(candidate, today=today, grace_day=grace_day)


def get_rent_period_panel_service(
    repo: RentPeriodRepository = Depends(get_rent_period_repository),
    admin_repo: AdministracionRepository = Depends(get_administracion_repository),
) -> RentPeriodPanelService:
    return RentPeriodPanelService(repo, admin_repo)


class DebtService:
    """RF-06 (`GET /debt`) + CA-02-05 (`GET /renters/:id/debt`): estado de
    deuda agregado por contrato (inquilino + propiedad)."""

    def __init__(self, repo: RentPeriodRepository, admin_repo: AdministracionRepository) -> None:
        self._repo = repo
        self._admin_repo = admin_repo

    async def _grace_day(self, organization_id: UUID) -> int:
        settings = await self._admin_repo.get_organization_settings(organization_id)
        if settings is None:  # pragma: no cover -- defensivo, la org del JWT siempre existe
            return _DEFAULT_GRACE_DAY
        return int(settings.get("grace_day", _DEFAULT_GRACE_DAY))

    async def _aggregate(
        self,
        *,
        organization_id: UUID,
        today: date,
        landlord_id: UUID | None,
        renter_id: UUID | None,
        min_days: int | None,
    ) -> list[DebtEntry]:
        grace_day = await self._grace_day(organization_id)
        candidates = await self._repo.list_candidates(
            organization_id=organization_id,
            unpaid_only=True,
            landlord_id=landlord_id,
            renter_id=renter_id,
        )

        by_contract: dict[UUID, list[RentPeriodPanelEntry]] = {}
        for candidate in candidates:
            entry = _to_panel_entry(candidate, today=today, grace_day=grace_day)
            by_contract.setdefault(entry.contract_id, []).append(entry)

        result: list[DebtEntry] = []
        for contract_id, periods in by_contract.items():
            # RF-06: "periodos adeudados, saldo, dias de mora e interes
            # sugerido acumulado" -- saldo e interes se SUMAN entre
            # periodos; `days_late` toma el periodo mas antiguo (el de
            # mayor mora, "desde cuando" debe).
            worst_days_late = max(p.days_late for p in periods)
            first = periods[0]
            debt_entry = DebtEntry(
                contract_id=contract_id,
                property_id=first.property_id,
                landlord_id=first.landlord_id,
                renter_id=first.renter_id,
                periods_overdue=len(periods),
                balance=sum((p.balance for p in periods), Decimal("0.00")),
                days_late=worst_days_late,
                suggested_interest=sum((p.suggested_interest for p in periods), Decimal("0.00")),
            )
            if min_days is not None and debt_entry.days_late < min_days:
                continue
            result.append(debt_entry)

        result.sort(key=lambda e: (-e.days_late, str(e.contract_id)))
        return result

    async def list_debt(
        self,
        *,
        organization_id: UUID,
        today: date,
        landlord_id: UUID | None,
        renter_id: UUID | None,
        min_days: int | None,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[DebtEntry], str | None]:
        """RF-06: `GET /debt?landlord_id=&renter_id=&min_days=` -- vista
        global de gestion de morosos (UC-10)."""
        entries = await self._aggregate(
            organization_id=organization_id,
            today=today,
            landlord_id=landlord_id,
            renter_id=renter_id,
            min_days=min_days,
        )
        start = _decode_index_cursor(cursor)
        page = entries[start : start + limit]
        next_cursor = _encode_index_cursor(start + limit) if start + limit < len(entries) else None
        return page, next_cursor

    async def renter_debt(
        self, renter_id: UUID, organization_id: UUID, *, today: date
    ) -> list[DebtEntry]:
        """CA-02-05: ficha del inquilino -- contratos con deuda (sin
        paginar: un inquilino tiene un numero acotado de contratos)."""
        return await self._aggregate(
            organization_id=organization_id,
            today=today,
            landlord_id=None,
            renter_id=renter_id,
            min_days=None,
        )


def get_debt_service(
    repo: RentPeriodRepository = Depends(get_rent_period_repository),
    admin_repo: AdministracionRepository = Depends(get_administracion_repository),
) -> DebtService:
    return DebtService(repo, admin_repo)


def _encode_index_cursor(index: int) -> str:
    """Cursor opaco (sdd_03 §"Paginacion": "cursor-based -- ?cursor=<opaque>")
    para reportes agregados en memoria (`RentPeriodPanelService`/
    `DebtService`): un indice entero base64, no un `(created_at, id)`
    real -- estas listas ya viven completas en memoria (join +
    calculo por fila), asi que paginar por indice sobre la lista ya
    ordenada es equivalente y evita reimplementar keyset pagination
    sobre columnas calculadas."""
    return base64.urlsafe_b64encode(str(index).encode("ascii")).decode("ascii")


def _decode_index_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    return int(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("ascii"))
