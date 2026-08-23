"""Acceso a datos de `attachments` (issue #26) -- generico, transversal.

RN-05 (spec_module_06_mantenimiento.md): "los adjuntos heredan los
permisos del pedido" -- este repository no decide permisos (eso lo hace
`requires_permission("attachment:manage")` en el router); solo persiste y
lee filas con filtro EXPLICITO de `organization_id` (RN-D01, defense in
depth sobre RLS), mismo criterio que el resto de los repositories del
repo (docs/skills/tenant-isolation.md).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from adminprop.db.session import get_tenant_db_session
from adminprop.shared.attachments.models import Attachment


class AttachmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Expuesto para que `modules/maintenance/service.py` reutilice la
        MISMA sesion al auditar/notificar en la misma transaccion (mismo
        criterio que `modules/payments/repository.py.session`)."""
        return self._session

    async def insert(
        self,
        *,
        organization_id: UUID,
        entity_type: str,
        entity_id: UUID,
        file_path: str,
        file_name: str,
        mime_type: str,
        size_bytes: int,
        uploaded_by: UUID,
    ) -> Attachment:
        row = Attachment(
            organization_id=organization_id,
            entity_type=entity_type,
            entity_id=entity_id,
            file_path=file_path,
            file_name=file_name,
            mime_type=mime_type,
            size_bytes=size_bytes,
            uploaded_by=uploaded_by,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get_by_id(self, attachment_id: UUID, organization_id: UUID) -> Attachment | None:
        """RN-D01: filtro explicito de `organization_id` -- usado por
        `GET /attachments/:id/download`."""
        stmt = select(Attachment).where(
            Attachment.id == attachment_id,
            Attachment.organization_id == organization_id,
            Attachment.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_entity(
        self, *, entity_type: str, entity_id: UUID, organization_id: UUID
    ) -> list[Attachment]:
        stmt = (
            select(Attachment)
            .where(
                Attachment.entity_type == entity_type,
                Attachment.entity_id == entity_id,
                Attachment.organization_id == organization_id,
                Attachment.deleted_at.is_(None),
            )
            .order_by(Attachment.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_entity(
        self, *, entity_type: str, entity_id: UUID, organization_id: UUID
    ) -> int:
        """spec_module_06_mantenimiento.md §Validaciones: "<= 10 por
        entidad" -- el service consulta esto ANTES de guardar un nuevo
        adjunto (`shared/storage/local.py.MAX_ATTACHMENTS_PER_ENTITY`)."""
        items = await self.list_by_entity(
            entity_type=entity_type, entity_id=entity_id, organization_id=organization_id
        )
        return len(items)

    async def commit(self) -> None:
        await self._session.commit()


def get_attachment_repository(
    session: AsyncSession = Depends(get_tenant_db_session),
) -> AttachmentRepository:
    return AttachmentRepository(session)
