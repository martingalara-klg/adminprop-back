"""Modelos SQLAlchemy 2.0 de `contracts` y `contract_adjustments` (issue #17).

SDD: infrastructure/spec_data_model.md §Capa 3 "Contratos". Mapean
exactamente las columnas creadas por la migracion
`20260815_110000_create_capa3_contratos.py` (issue #16) -- ningun DDL
nuevo aca, solo la capa ORM que faltaba (esa migracion documenta
explicitamente que el issue #17 es quien la consume).

`ContractAdjustment` se declara ya en este PR (aunque su flujo de
aplicacion -- RF-04 -- es del issue #18) para que el modelo ORM este
disponible sin otra migracion ni PR intermedio; ver decision en
`service.py` sobre por que el modulo `contracts` no expone endpoints de
ajustes todavia.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, DateTime, Index, Numeric, SmallInteger, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from adminprop.db.base import Base


class Contract(Base):
    """spec_data_model.md §Capa 3 "contracts" -- el contrato de locacion.

    Sin `ForeignKey(...)` a nivel de objeto SQLAlchemy para
    `property_id`/`renter_id` -- mismo criterio que
    `modules/properties/models.py.Property.landlord_id` (el FK real ya lo
    crea la migracion #16 a nivel de DDL). `current_amount` solo se
    modifica via ajuste (RN-C04) -- este modulo no expone esa mutacion,
    solo la protege en `service.update`.
    """

    __tablename__ = "contracts"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    property_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    renter_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    initial_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    current_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    daily_late_fee_pct: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    adjustment_frequency_months: Mapped[int | None] = mapped_column(SmallInteger)
    adjustment_index: Mapped[str | None] = mapped_column(Text)
    adjustment_index_notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # `DateTime(timezone=True)`: la columna DB es TIMESTAMPTZ -- mismo
    # motivo documentado en `modules/properties/models.py.Property`.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        Index("ix_contracts_organization_id_orm", "organization_id"),
        Index("ix_contracts_org_property_id_orm", "organization_id", "property_id"),
    )


class ContractAdjustment(Base):
    """spec_data_model.md §Capa 3 "contract_adjustments" -- historial de
    ajustes por indice. Sin `deleted_at`: la migracion #16 no la declara
    para esta tabla (correcciones se modelan como un ajuste nuevo con
    nota, nunca borrado). Fuera de alcance de este issue el flujo de
    creacion/aplicacion (RF-04, issue #18) -- este modelo solo deja la
    capa ORM lista."""

    __tablename__ = "contract_adjustments"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    contract_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    due_period: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    pct_applied: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    previous_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    new_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    notes: Mapped[str | None] = mapped_column(Text)
    applied_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    __table_args__ = (
        Index("ix_contract_adjustments_organization_id_orm", "organization_id"),
        Index("ix_contract_adjustments_contract_id_orm", "contract_id"),
    )
