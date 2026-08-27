"""Modelos SQLAlchemy 2.0 de `properties` y `property_service_accounts` (issue #15).

SDD: infrastructure/spec_data_model.md §Capa 2 "Propiedades". Mapean
exactamente las columnas creadas por la migracion
`20260815_100000_create_capa2_propiedades.py` (issue #14) -- ningun DDL
nuevo aca, solo la capa ORM que faltaba (esa migracion documenta
explicitamente que el issue #15 es quien la consume).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from adminprop.db.base import Base


class Property(Base):
    """spec_data_model.md §Capa 2 "properties" -- el inmueble administrado.

    Sin `ForeignKey(...)` a nivel de objeto SQLAlchemy para `landlord_id`
    a proposito -- mismo criterio documentado en
    `modules/people/models.py.Landlord` (el FK real ya lo crea la
    migracion #14 a nivel de DDL; declararlo tambien en el mapper agrega
    una dependencia de orden de import entre `people` y `properties` sin
    beneficio real, dado que ningun query de este modulo necesita
    `relationship()` navegable). `status` se persiste (no es una
    `@property` calculada): RF-04 lo actualiza el modulo de contratos
    (issue #17, todavia inexistente) -- este modulo solo lee/escribe el
    valor tal cual esta en la fila."""

    __tablename__ = "properties"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    landlord_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # Issue #99: nullable en DB (datos legacy preexistentes) -- sin
    # `ForeignKey(...)` a nivel de mapper, mismo criterio que `landlord_id`
    # (el FK real ya lo crea la migracion `20260827_100000`).
    neighborhood_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    address: Mapped[str] = mapped_column(Text, nullable=False)
    property_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # `DateTime(timezone=True)`: la columna DB es TIMESTAMPTZ -- mismo
    # motivo documentado en `modules/people/models.py` (asyncpg + datetime
    # aware de `soft_delete` contra un tipo SQL sin tz revienta con
    # DataError si falta `timezone=True`).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (Index("ix_properties_organization_id_orm", "organization_id"),)


class Neighborhood(Base):
    """spec_data_model.md §Capa 2 "neighborhoods" -- catalogo de barrios
    parametrizable por organizacion (issue #99). Mapea las columnas
    creadas por `20260827_100000_create_neighborhoods_and_alter_properties.py`.
    """

    __tablename__ = "neighborhoods"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (Index("ix_neighborhoods_organization_id_orm", "organization_id"),)


class PropertyServiceAccount(Base):
    """spec_data_model.md §Capa 2 "property_service_accounts" -- numeros de
    cuenta de servicios/impuestos, puramente informativos (RF-02, UC-01).
    `secondary_number` nullable (caso `luz`: n° de contrato adicional al
    n° de cliente)."""

    __tablename__ = "property_service_accounts"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    property_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    service_type: Mapped[str] = mapped_column(Text, nullable=False)
    account_number: Mapped[str] = mapped_column(Text, nullable=False)
    secondary_number: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        Index("ix_property_service_accounts_organization_id_orm", "organization_id"),
        Index("ix_property_service_accounts_org_property_orm", "organization_id", "property_id"),
    )
