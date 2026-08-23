"""Modelos SQLAlchemy 2.0 de `settlements` y `settlement_line_items`
(issue #29).

SDD: infrastructure/spec_data_model.md §Capa 6 "settlements"/
"settlement_line_items" + core/sdd_02_domain_model.md §2.15 (Settlement /
SettlementLineItem). Mapean exactamente las columnas creadas por la
migracion `20260820_090000_create_capa6_liquidaciones.py` (issue #27,
cuyo docstring documenta explicitamente que el issue #29 es quien agrega
el modelo ORM -- mismo criterio que `modules/charges/models.py` respecto
de esa misma migracion).

Sin `deleted_at` en ninguna de las dos: Apendice B de spec_data_model.md
declara "Sin delete" -- RN-L03, la correccion es una regeneracion
auditada (issue #30), nunca un borrado.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, DateTime, Index, Numeric, SmallInteger, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from adminprop.db.base import Base


class Settlement(Base):
    """spec_data_model.md §Capa 6 "settlements" -- la liquidacion mensual
    por propietario, toda en ARS (UC-12, RN-L01, RN-L06). `status` solo
    admite `draft`/`issued` (CHECK de la migracion #27): los estados del
    JOB de generacion asincrona (`pending`/`processing`/`completed`/
    `with_errors`/`failed`, RF-01) NO son valores de esta columna -- se
    trackean fuera de la fila (ver `job_status.py`), documentado como
    decision de implementacion del PR (no hay columna de metadata/job
    status en el schema migrado y este issue no agrega migraciones)."""

    __tablename__ = "settlements"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    landlord_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    period: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    exchange_rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    total_collected: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )
    commission_total: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )
    charges_total: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )
    repairs_total: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )
    already_settled_total: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )
    net_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )
    commission_pct_used: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    regenerated_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    generated_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    __table_args__ = (
        # Declarada tambien a nivel ORM (la DDL real ya la creo la
        # migracion #27) -- mismo criterio que
        # `modules/payments/models.py.RentPeriod` con
        # `rent_periods_contract_period_unique`.
        UniqueConstraint("landlord_id", "period", name="settlements_landlord_period_unique"),
        Index("ix_settlements_organization_id_orm", "organization_id"),
    )


class SettlementLineItem(Base):
    """spec_data_model.md §Capa 6 "settlement_line_items" -- el detalle
    linea por linea de la liquidacion. `source_entity_id` es polimorfica
    (sin FK fisica, igual que `attachments.entity_id`) -- referencia a
    `payment` / `charge_entry` / `work_order` segun `line_type`."""

    __tablename__ = "settlement_line_items"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    settlement_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    line_type: Mapped[str] = mapped_column(Text, nullable=False)
    property_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    source_entity_type: Mapped[str | None] = mapped_column(Text)
    source_entity_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    original_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    original_currency: Mapped[str] = mapped_column(Text, nullable=False)
    amount_ars: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    __table_args__ = (Index("ix_settlement_line_items_settlement_id_orm", "settlement_id"),)
