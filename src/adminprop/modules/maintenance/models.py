"""Modelos SQLAlchemy 2.0 de `work_orders` y `work_order_quotes` (issue #26).

SDD: infrastructure/spec_data_model.md §Capa 5 "work_orders"/"work_order_quotes"
+ core/sdd_02_domain_model.md §2.12 (WorkOrder) / §2.13 (WorkOrderQuote).
Mapean exactamente las columnas creadas por la migracion
`20260819_150000_create_capa5_mantenimiento.py` (issue #25, cuyo
docstring documenta explicitamente que el issue #26 agrega el modelo ORM
-- mismo criterio que `modules/payments/models.py` respecto de la
migracion #20).

`Attachment` NO vive aca -- ver `shared/attachments/models.py` (tabla
polimorfica transversal, no propiedad exclusiva de este modulo).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from adminprop.db.base import Base


class WorkOrder(Base):
    """spec_data_model.md §Capa 5 "work_orders" -- el pedido de reparacion
    con su ciclo completo (RF-01..RF-05): `open` -> `in_progress` ->
    `closed` | `cancelled`. `approved_quote_id` se setea al aprobar una
    cotizacion (RF-03); `final_cost` nace `NULL` y se completa al cierre
    (RF-04, default = monto de la cotizacion aprobada). Sin
    `settled_in_settlement_id` (columna diferida a la Capa 6, issue #27 --
    ver `settlement_hook.py` para el punto de extension de CA-06-07)."""

    __tablename__ = "work_orders"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    property_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    payer: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'open'"))
    final_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    # FK declarada a nivel ORM (la migracion ya la crea via ALTER TABLE
    # posterior a `work_order_quotes` -- mismo motivo documentado alli).
    approved_quote_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("work_order_quotes.id")
    )
    created_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        Index("ix_work_orders_organization_id_orm", "organization_id"),
        Index("ix_work_orders_property_id_orm", "property_id"),
    )


class WorkOrderQuote(Base):
    """spec_data_model.md §Capa 5 "work_order_quotes" -- las cotizaciones
    del encargado (RF-02): `submitted` -> `approved` | `discarded`. Sin
    `deleted_at` (Apendice B no la lista -- la baja logica es el propio
    `status = 'discarded'`, RF-03)."""

    __tablename__ = "work_order_quotes"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    work_order_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'submitted'"))
    submitted_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    __table_args__ = (
        Index("ix_work_order_quotes_organization_id_orm", "organization_id"),
        Index("ix_work_order_quotes_work_order_id_orm", "work_order_id"),
    )
