"""Modelo SQLAlchemy 2.0 de `rent_periods` (issue #21).

SDD: infrastructure/spec_data_model.md §Capa 4 "rent_periods" +
core/sdd_02_domain_model.md §2.9. Mapea exactamente las columnas creadas
por la migracion `20260819_140000_create_capa4_cobranzas.py` (issue #20,
cuyo docstring documenta explicitamente que este issue -- #21 -- es
quien agrega el modelo ORM, mismo criterio que `modules/contracts/models.py`
documenta para `Contract`/`ContractAdjustment` respecto de la migracion
#16). Sin `Payment` todavia: esa tabla ya existe (misma migracion) pero
su capa ORM/repository/service es alcance de los issues #22/#23 (RF-03
en adelante de spec_module_04_cobranzas.md).

Sin `deleted_at`: Apendice B de spec_data_model.md declara "Sin delete"
para `rent_periods` -- los periodos se corrigen/regeneran, nunca se
borran.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, DateTime, Index, Numeric, Text, UniqueConstraint, text
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
