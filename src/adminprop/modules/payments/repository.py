"""Acceso a datos de `rent_periods` (issue #21) y `payments` (issue #22).

SDD: infrastructure/spec_data_model.md §Capa 4. docs/skills/tenant-isolation.md
("todo metodo del repository recibe organization_id como parametro y lo
aplica en el WHERE" -- defense in depth sobre RLS, RN-D01).
docs/skills/async-worker.md ("jobs Beat idempotentes -- las UNIQUE
constraints los protegen"): `insert_pending` es el INSERT idempotente que
tanto el job mensual `generate_rent_periods` (issue #21,
`workers/notification_worker.py`) como los hooks de contratos
(`modules/contracts/rent_period_hook.py`, issues #17/#18) usan para
respetar RN-P01 (un solo `rent_period` por `contract_id`+`period`).

`get_by_id`/`update_after_payment`/`commit` de `RentPeriodRepository`, y
la clase `PaymentRepository` completa, son del issue #22 -- el #21
deliberadamente no los agrego (su unico consumidor era el job Beat, que
maneja su propio commit via `session.begin()` del caller).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from adminprop.db.session import get_tenant_db_session
from adminprop.modules.payments.models import Payment, RentPeriod


class RentPeriodRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def insert_pending(
        self,
        *,
        organization_id: UUID,
        contract_id: UUID,
        period: date,
        amount_due: Decimal,
        currency: str,
    ) -> UUID | None:
        """RN-P01 (UNIQUE `rent_periods_contract_period_unique` sobre
        `(contract_id, period)`, migracion #20): INSERT idempotente via
        `ON CONFLICT DO NOTHING` (sdd_04 §1.3 -- "Jobs Beat idempotentes
        [...] las UNIQUE constraints los protegen"). `status` nace
        `pending` (default de la columna, RF-01). Devuelve el `id` del
        `rent_period` recien creado, o `None` si ya existia (conflicto) --
        el caller no necesita diferenciar "ya existia" de "se creo ahora"
        salvo para loggear/contar en tests.
        """
        stmt = (
            pg_insert(RentPeriod)
            .values(
                organization_id=organization_id,
                contract_id=contract_id,
                period=period,
                amount_due=amount_due,
                currency=currency,
            )
            .on_conflict_do_nothing(
                index_elements=[RentPeriod.contract_id, RentPeriod.period],
            )
            .returning(RentPeriod.id)
        )
        result = await self._session.execute(stmt)
        row = result.first()
        return row.id if row is not None else None

    async def get_by_contract_and_period(
        self, contract_id: UUID, organization_id: UUID, period: date
    ) -> RentPeriod | None:
        """Usado por tests (y por el futuro RF-02 panel de cobranzas) para
        verificar el `rent_period` generado -- filtro explicito de
        `organization_id` (RN-D01) ademas del RLS."""
        stmt = select(RentPeriod).where(
            RentPeriod.contract_id == contract_id,
            RentPeriod.organization_id == organization_id,
            RentPeriod.period == period,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, rent_period_id: UUID, organization_id: UUID) -> RentPeriod | None:
        """RN-D01: filtro explicito de `organization_id` (defense in depth
        sobre RLS) -- usado por `PaymentService` (issue #22) para resolver
        el periodo a cobrar/previsualizar."""
        stmt = select(RentPeriod).where(
            RentPeriod.id == rent_period_id,
            RentPeriod.organization_id == organization_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_after_payment(
        self,
        rent_period_id: UUID,
        organization_id: UUID,
        *,
        paid_total: Decimal,
        status: str,
    ) -> RentPeriod | None:
        """RF-03, RN-P05: recalcula `paid_total`/`status` tras imputar un
        cobro (`pending`/`partial` -> `partial`/`paid`, CA-04-04/CA-04-05).
        Filtro explicito de `organization_id` (RN-D01)."""
        row = await self.get_by_id(rent_period_id, organization_id)
        if row is None:
            return None
        row.paid_total = paid_total
        row.status = status
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def commit(self) -> None:
        await self._session.commit()


class PaymentRepository:
    """Acceso a datos de `payments` (issue #22, RF-03/RF-04)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Expuesto para que `service.py` pase la MISMA sesion a
        `AuditService.audit()` -- mismo criterio que
        `modules/contracts/repository.py.ContractRepository.session`."""
        return self._session

    async def insert(
        self,
        *,
        organization_id: UUID,
        rent_period_id: UUID,
        payment_date: date,
        method: str,
        payment_currency: str,
        amount: Decimal,
        exchange_rate: Decimal | None,
        destination: str,
        suggested_interest: Decimal,
        charged_interest: Decimal,
        forgiven_interest: Decimal,
        days_late: int,
        notes: str | None,
        created_by: UUID,
    ) -> Payment:
        """RF-03/RF-04: persiste el cobro con los tres valores de interes
        (RN-P04) y el TC usado (RN-P06) -- inmutable una vez creado
        (RN-06/RN-D04, la correccion es anular + recargar, issue #23)."""
        row = Payment(
            organization_id=organization_id,
            rent_period_id=rent_period_id,
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
            created_by=created_by,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get_by_id(self, payment_id: UUID, organization_id: UUID) -> Payment | None:
        """RN-D01: filtro explicito de `organization_id` -- usado por el
        futuro RF-05 (anulacion, issue #23) y por tests."""
        stmt = select(Payment).where(
            Payment.id == payment_id,
            Payment.organization_id == organization_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def commit(self) -> None:
        await self._session.commit()


def get_rent_period_repository(
    session: AsyncSession = Depends(get_tenant_db_session),
) -> RentPeriodRepository:
    return RentPeriodRepository(session)


def get_payment_repository(
    session: AsyncSession = Depends(get_tenant_db_session),
) -> PaymentRepository:
    return PaymentRepository(session)
