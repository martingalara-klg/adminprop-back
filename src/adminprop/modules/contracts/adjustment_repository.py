"""Acceso a datos de `contract_adjustments` (issue #18, RF-04).

SDD: infrastructure/spec_data_model.md §Capa 3 "contract_adjustments" +
core/sdd_02_domain_model.md §2.8. docs/skills/tenant-isolation.md ("todo
metodo del repository recibe organization_id como parametro y lo aplica
en el WHERE" -- defense in depth sobre RLS, RN-D01). Reutiliza el modelo
ORM `ContractAdjustment` ya declarado en `modules/contracts/models.py`
(issue #17, dejado listo a proposito para este issue).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from adminprop.db.session import get_tenant_db_session
from adminprop.modules.contracts.models import Contract, ContractAdjustment


def _encode_cursor(created_at: datetime, row_id: UUID) -> str:
    raw = f"{created_at.isoformat()}|{row_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
    created_at_raw, row_id_raw = raw.split("|", 1)
    return datetime.fromisoformat(created_at_raw), UUID(row_id_raw)


@dataclass(frozen=True)
class DueContractRow:
    """Contrato candidato a ajuste, resuelto por `list_ars_contracts_due_for_adjustment_check`.

    Solo los campos que `AdjustmentService.detect_due_adjustments` necesita
    para calcular el proximo `due_period` (RN-C03: contado desde el inicio
    o desde el ultimo ajuste `applied`)."""

    id: UUID
    start_date: date
    current_amount: Decimal
    adjustment_frequency_months: int


class ContractAdjustmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Expuesto para que `service.py` reutilice la MISMA sesion en
        `AuditService.audit()` y `notifications.service.emit()` -- mismo
        criterio que `ContractRepository.session`."""
        return self._session

    # ─── RF-04 paso 1: deteccion (`detect_due_adjustments`) ────────────────

    async def list_ars_contracts_due_for_adjustment_check(
        self, organization_id: UUID
    ) -> list[DueContractRow]:
        """Contratos candidatos: `active`, ARS, con frecuencia de ajuste
        configurada, no borrados. RN-03/RN-C02 ya garantiza que un USD
        nunca tiene `adjustment_frequency_months` (CHECK de DB), pero se
        filtra igual de forma explicita para no depender solo del CHECK."""
        stmt = select(Contract).where(
            Contract.organization_id == organization_id,
            Contract.status == "active",
            Contract.currency == "ARS",
            Contract.adjustment_frequency_months.is_not(None),
            Contract.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return [
            DueContractRow(
                id=row.id,
                start_date=row.start_date,
                current_amount=row.current_amount,
                adjustment_frequency_months=row.adjustment_frequency_months,
            )
            for row in result.scalars().all()
        ]

    async def get_last_applied_adjustment_due_period(
        self, contract_id: UUID, organization_id: UUID
    ) -> date | None:
        """Ancla de calculo (RN-C03): el `due_period` del ultimo ajuste
        `applied` del contrato, o `None` si nunca se aplico uno (el
        service usa `contract.start_date` como ancla en ese caso)."""
        stmt = (
            select(ContractAdjustment.due_period)
            .where(
                ContractAdjustment.contract_id == contract_id,
                ContractAdjustment.organization_id == organization_id,
                ContractAdjustment.status == "applied",
            )
            .order_by(ContractAdjustment.due_period.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def has_pending_adjustment(self, contract_id: UUID, organization_id: UUID) -> bool:
        """Refleja el indice parcial unico `idx_contract_adjustments_one_pending_per_contract`
        (migracion #16, CA-16-03) a nivel app -- evita el round-trip que
        fallaria contra la DB y permite un mensaje de dominio claro."""
        stmt = select(ContractAdjustment.id).where(
            ContractAdjustment.contract_id == contract_id,
            ContractAdjustment.organization_id == organization_id,
            ContractAdjustment.status == "pending",
        )
        result = await self._session.execute(stmt)
        return result.first() is not None

    async def create_pending(
        self,
        *,
        organization_id: UUID,
        contract_id: UUID,
        due_period: date,
        previous_amount: Decimal,
    ) -> ContractAdjustment:
        """RN-C03: el ajuste nace `pending`, sin `pct_applied`/`new_amount`
        -- el operador ingresa el % por fuera (nunca automatico)."""
        row = ContractAdjustment(
            organization_id=organization_id,
            contract_id=contract_id,
            due_period=due_period,
            status="pending",
            previous_amount=previous_amount,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    # ─── RN-08/RN-C06 (issue #100): ajuste sintetico de carga inicial ──────

    async def create_applied_initial(
        self,
        *,
        organization_id: UUID,
        contract_id: UUID,
        due_period: date,
        previous_amount: Decimal,
        new_amount: Decimal,
        actor_user_id: UUID,
    ) -> ContractAdjustment:
        """RN-08/RN-C06 (spec_module_03_contratos.md RF-02/RF-04 paso 6):
        registra el ajuste `applied` sintetico de "alta de contrato en
        curso" -- llamado por `ContractService.create` inmediatamente
        despues de insertar el `Contract` (misma transaccion). Se
        distingue de un ajuste aplicado manualmente (`apply()` arriba,
        RF-04 paso 4) en dos puntos: `pct_applied` queda NULL (no hubo %
        que calcular, es un valor declarado) y `notes` lleva el prefijo
        `"Carga inicial:"`. Este ajuste es el ancla que usa
        `get_last_applied_adjustment_due_period` para el proximo ajuste
        periodico (RN-C03) -- sin modificar esa consulta."""
        row = ContractAdjustment(
            organization_id=organization_id,
            contract_id=contract_id,
            due_period=due_period,
            status="applied",
            previous_amount=previous_amount,
            new_amount=new_amount,
            notes="Carga inicial: monto vigente declarado al alta del contrato (RN-C06).",
            applied_by=actor_user_id,
            applied_at=datetime.now(UTC),
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    # ─── RF-04 paso 3/5: bandeja + aplicacion + historial ──────────────────

    async def get_by_id(
        self, adjustment_id: UUID, organization_id: UUID
    ) -> ContractAdjustment | None:
        stmt = select(ContractAdjustment).where(
            ContractAdjustment.id == adjustment_id,
            ContractAdjustment.organization_id == organization_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_contract(
        self, contract_id: UUID, organization_id: UUID
    ) -> list[ContractAdjustment]:
        """`GET /contracts/:id/adjustments` -- historial completo, mas
        reciente primero (sdd_02 §2.8: applied es inmutable/append-only,
        sin paginacion cursor -- el volumen por contrato es acotado por
        `adjustment_frequency_months`)."""
        stmt = (
            select(ContractAdjustment)
            .where(
                ContractAdjustment.contract_id == contract_id,
                ContractAdjustment.organization_id == organization_id,
            )
            .order_by(ContractAdjustment.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_pending(
        self, *, organization_id: UUID, cursor: str | None, limit: int
    ) -> tuple[list[ContractAdjustment], str | None]:
        """`GET /adjustments?status=pending` -- la bandeja de ajustes que
        tocan (sdd_03 §8)."""
        stmt = select(ContractAdjustment).where(
            ContractAdjustment.organization_id == organization_id,
            ContractAdjustment.status == "pending",
        )
        if cursor:
            cursor_created_at, cursor_id = _decode_cursor(cursor)
            stmt = stmt.where(
                (ContractAdjustment.created_at, ContractAdjustment.id)
                < (cursor_created_at, cursor_id)
            )
        stmt = stmt.order_by(
            ContractAdjustment.created_at.desc(), ContractAdjustment.id.desc()
        ).limit(limit + 1)

        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = (
            _encode_cursor(page[-1].created_at, page[-1].id) if has_more and page else None
        )
        return page, next_cursor

    async def apply(
        self,
        adjustment_id: UUID,
        organization_id: UUID,
        *,
        pct: Decimal,
        new_amount: Decimal,
        actor_user_id: UUID,
    ) -> ContractAdjustment | None:
        """RN-C03: `pending -> applied`, inmutable a partir de aca (sdd_02
        §2.8) -- el service ya valido que el ajuste este `pending`."""
        row = await self.get_by_id(adjustment_id, organization_id)
        if row is None:  # pragma: no cover -- defensivo, service ya valido existencia
            return None
        row.status = "applied"
        row.pct_applied = pct
        row.new_amount = new_amount
        row.applied_by = actor_user_id
        row.applied_at = datetime.now(UTC)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def commit(self) -> None:
        await self._session.commit()


def get_contract_adjustment_repository(
    session: AsyncSession = Depends(get_tenant_db_session),
) -> ContractAdjustmentRepository:
    return ContractAdjustmentRepository(session)
