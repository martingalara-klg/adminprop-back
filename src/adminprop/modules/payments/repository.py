"""Acceso a datos de `rent_periods` (issue #21).

SDD: infrastructure/spec_data_model.md §Capa 4. docs/skills/tenant-isolation.md
("todo metodo del repository recibe organization_id como parametro y lo
aplica en el WHERE" -- defense in depth sobre RLS, RN-D01).
docs/skills/async-worker.md ("jobs Beat idempotentes -- las UNIQUE
constraints los protegen"): `insert_pending` es el INSERT idempotente que
tanto el job mensual `generate_rent_periods` (issue #21,
`workers/notification_worker.py`) como los hooks de contratos
(`modules/contracts/rent_period_hook.py`, issues #17/#18) usan para
respetar RN-P01 (un solo `rent_period` por `contract_id`+`period`).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from adminprop.modules.payments.models import RentPeriod


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
