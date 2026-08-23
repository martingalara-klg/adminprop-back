"""Modelos SQLAlchemy 2.0 de `recurring_charges` y `charge_entries` (issue #28).

SDD: infrastructure/spec_data_model.md §Capa 6 "recurring_charges"/
"charge_entries" + core/sdd_02_domain_model.md §2.11 (RecurringCharge /
ChargeEntry). Mapean exactamente las columnas creadas por la migracion
`20260820_090000_create_capa6_liquidaciones.py` (issue #27, cuyo
docstring documenta explicitamente que el issue #28 es quien agrega el
modelo ORM -- mismo criterio que `modules/payments/models.py` documenta
para `RentPeriod`/`Payment` respecto de la migracion #20).

`recurring_charges` tiene `deleted_at` (Apendice B de spec_data_model.md).
`charge_entries` no: "Sin delete" -- se corrigen/regeneran, nunca se
borran (RN-D04, la correccion es un PATCH auditado, no un borrado).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, DateTime, Index, Numeric, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from adminprop.db.base import Base


class RecurringCharge(Base):
    """spec_data_model.md §Capa 6 "recurring_charges" -- el concepto
    recurrente de la propiedad (rentas, municipalidad, otro; UC-11). Un
    concepto `is_active=false` deja de aparecer en la carga mensual
    (RF-05) pero conserva su historial de `charge_entries`."""

    __tablename__ = "recurring_charges"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    property_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    charge_type: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "ix_recurring_charges_organization_id_orm",
            "organization_id",
        ),
    )


class ChargeEntry(Base):
    """spec_data_model.md §Capa 6 "charge_entries" -- el importe del mes de
    un concepto, ingresado a mano (UC-11). RF-05: unico por
    `(recurring_charge_id, period)` (UNIQUE
    `charge_entries_recurring_charge_period_unique`, migracion #27) ->
    `409 CHARGE_ENTRY_ALREADY_EXISTS` al duplicar (CA-05-08). Sin
    `deleted_at`: la correccion es un PATCH auditado (RN-D04), nunca un
    borrado."""

    __tablename__ = "charge_entries"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    recurring_charge_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    period: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    __table_args__ = (
        # Declarada tambien a nivel ORM (aunque la DDL real ya la creo la
        # migracion #27) -- mismo criterio que
        # `modules/payments/models.py.RentPeriod` con
        # `rent_periods_contract_period_unique`: fuente unica de verdad de
        # las columnas del constraint para el chequeo app-level de
        # `repository.py` (sin duplicar los nombres de columna a mano).
        UniqueConstraint(
            "recurring_charge_id",
            "period",
            name="charge_entries_recurring_charge_period_unique",
        ),
        Index("ix_charge_entries_organization_id_orm", "organization_id"),
        Index("ix_charge_entries_organization_period_orm", "organization_id", "period"),
    )
