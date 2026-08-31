"""Modelos SQLAlchemy 2.0 de `rent_periods` (issue #21) y `payments`
(issue #22).

SDD: infrastructure/spec_data_model.md §Capa 4 "rent_periods"/"payments" +
core/sdd_02_domain_model.md §2.9 (RentPeriod) / §2.10 (Payment). Mapean
exactamente las columnas creadas por la migracion
`20260819_140000_create_capa4_cobranzas.py` (issue #20, cuyo docstring
documenta explicitamente que los issues #21/#22 son quienes agregan el
modelo ORM, mismo criterio que `modules/contracts/models.py` documenta
para `Contract`/`ContractAdjustment` respecto de la migracion #16).

Sin `deleted_at`: Apendice B de spec_data_model.md declara "Sin delete"
para `rent_periods` -- los periodos se corrigen/regeneran, nunca se
borran. `payments` tampoco tiene `deleted_at`: su baja logica es
`voided_at`/`voided_by` (RN-D04, anulacion auditada -- issue #23, fuera
de alcance de este PR, que solo declara las columnas).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Date,
    DateTime,
    Index,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from adminprop.db.base import Base


class RentPeriod(Base):
    """spec_data_model.md §Capa 4 "rent_periods" -- el alquiler de un mes
    de un contrato (RN-P01: unico por `(contract_id, period)`, UNIQUE
    constraint `rent_periods_contract_period_unique` de la migracion
    #20). `amount_due`/`currency` son una copia congelada del monto
    vigente del contrato al momento de generarse (sdd_02 §2.9) -- no se
    recalculan si el contrato cambia despues (un ajuste posterior solo
    afecta a `rent_periods` futuros, nunca a este)."""

    __tablename__ = "rent_periods"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    contract_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    period: Mapped[date] = mapped_column(Date, nullable=False)
    amount_due: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    paid_total: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )
    # `DateTime(timezone=True)`: la columna DB es TIMESTAMPTZ -- mismo
    # motivo documentado en `modules/contracts/models.py.Contract`.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    __table_args__ = (
        # Declarada tambien a nivel ORM (aunque la DDL real ya la creo la
        # migracion #20) para que `on_conflict_do_nothing(index_elements=...)`
        # del repository tenga una fuente unica de verdad sobre las columnas
        # del constraint, sin duplicar los nombres de columna a mano.
        UniqueConstraint("contract_id", "period", name="rent_periods_contract_period_unique"),
        Index("ix_rent_periods_organization_id_orm", "organization_id"),
    )


class Payment(Base):
    """spec_data_model.md §Capa 4 "payments" -- la imputacion de un cobro
    contra un `rent_period` (RF-03/RF-04, issue #22). `suggested_interest`/
    `charged_interest`/`forgiven_interest` quedan siempre los tres
    registrados (RN-P04); `days_late` es el dato congelado al momento del
    pago (RN-P02). Sin `deleted_at`: la baja logica es `voided_at`/
    `voided_by` (RN-D04, anulacion -- issue #23, este PR solo declara las
    columnas)."""

    __tablename__ = "payments"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    rent_period_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    payment_currency: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    exchange_rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    destination: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_interest: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )
    charged_interest: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )
    forgiven_interest: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )
    days_late: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    notes: Mapped[str | None] = mapped_column(Text)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # issue #119, RN-P09: 'manual' (default, cobro registrado por un
    # operador) | 'initial_load' (generado automaticamente al declarar la
    # carga inicial de un contrato en curso -- ver
    # `contracts/rent_period_hook.py.generate_initial_load_history`).
    # Fuente de verdad para excluir de liquidaciones/recibos/anulacion.
    origin: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'manual'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    __table_args__ = (
        Index("ix_payments_organization_id_orm", "organization_id"),
        Index("ix_payments_rent_period_id_orm", "rent_period_id"),
    )
