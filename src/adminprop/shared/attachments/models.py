"""Modelo SQLAlchemy 2.0 de `attachments` (issue #26).

SDD: infrastructure/spec_data_model.md §Capa 5 "attachments". Mapea
exactamente las columnas creadas por la migracion
`20260819_150000_create_capa5_mantenimiento.py` (issue #25, cuyo
docstring documenta explicitamente que el issue #26 es quien agrega el
modelo ORM -- mismo criterio que `modules/payments/models.py` respecto de
la migracion #20).

Vive en `shared/` (no en `modules/maintenance/`) a proposito: la tabla es
polimorfica desde su diseno (`entity_type` CHECK incluye `work_order`,
`work_order_quote`, `settlement`, `payment`, `renter`) y sus consumidores
YA trascienden mantenimiento desde el issue #24
(`modules/payments/attachment_hook.py`, `modules/people/attachment_hook.py`,
todavia no-op -- ver "Decisiones de implementacion" del PR de este issue
para por que no se cablearon en este PR). Ponerla en `shared/` evita que
esos otros modulos tengan que importar `modules/maintenance/*` para
guardar un adjunto propio -- mismo criterio arquitectonico que
`shared/notifications/` y `shared/audit/` (servicios transversales que
cualquier modulo consume sin acoplarse a otro modulo de negocio)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Index, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from adminprop.db.base import Base


class Attachment(Base):
    """spec_data_model.md §Capa 5 "attachments" -- archivo generico
    (fotos de pedidos/cotizaciones, PDFs de recibos/liquidaciones)
    asociado polimorficamente a una entidad (`entity_type` + `entity_id`,
    SIN FK fisica en `entity_id` -- integridad app-level, tal como
    documenta la migracion). `file_path` apunta al filesystem local
    (volumen Docker, `shared/storage/local.py`) -- nunca el binario en la
    fila (RN-D02/Apendice B: soft delete con `deleted_at`, sin DELETE
    fisico)."""

    __tablename__ = "attachments"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uploaded_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        Index("ix_attachments_organization_id_orm", "organization_id"),
        Index("ix_attachments_entity_type_entity_id_orm", "entity_type", "entity_id"),
    )
