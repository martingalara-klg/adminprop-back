"""Modelos SQLAlchemy 2.0 de `landlords` y `renters` (issue #13).

SDD: infrastructure/spec_data_model.md §Capa 1 "Personas". Mapean
exactamente las columnas creadas por la migracion
`20260815_090000_create_capa1_personas.py` (issue #12) -- ningun DDL nuevo
aca, solo la capa ORM que faltaba (esa migracion documenta explicitamente
que el consumidor del cifrado de `bank_info` es este issue).

Primer modulo del repo que declara modelos ORM reales (`db/base.py`:
"No hay modelos ORM todavia... las tablas de negocio llegan en el issue
#5" -- landlords/renters son la primera tabla de negocio con un dueno de
modulo claro, a diferencia de `roles`/`organization_members`/etc. que
`modules/administracion` deja en SQL crudo por ser compartidas entre
varios modulos sin dueno unico).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ForeignKey, Index, LargeBinary, Numeric, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from adminprop.db.base import Base


class Landlord(Base):
    """spec_data_model.md §Capa 1 "landlords" -- el propietario, a quien
    se le rinde. `bank_info` persiste como BYTEA (ciphertext pgcrypto,
    ver `shared/encryption/pgcrypto.py`); el repository es quien
    cifra/descifra, nunca este modelo (sin metodos custom, CLAUDE.md §3)."""

    __tablename__ = "landlords"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    tax_id: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    bank_info: Mapped[bytes | None] = mapped_column(LargeBinary)
    commission_pct: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)

    __table_args__ = (Index("ix_landlords_organization_id_orm", "organization_id"),)


class Renter(Base):
    """spec_data_model.md §Capa 1 "renters" -- el inquilino. Sin datos
    bancarios ni comision (no aplica: no se le rinde a el)."""

    __tablename__ = "renters"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    tax_id: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)

    __table_args__ = (Index("ix_renters_organization_id_orm", "organization_id"),)
