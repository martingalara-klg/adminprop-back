"""Logica del panel in-app de notificaciones (issue #31).

SDD: infrastructure/spec_notificaciones.md RF-02.
Implements: CA-NT-01 (via `GET /notifications`, el emisor real es
            `shared/notifications/service.emit`), CA-NT-04 (badge +
            read-all).

No hace SQL: orquesta `shared.notifications.repository.NotificationRepository`
(mismo repository de `emit()`, ver su docstring) + el cache del badge
(`shared/notifications/unread_cache.py`, sdd_04 §1.4).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import Depends

from adminprop.shared.errors.codes import NotFoundException
from adminprop.shared.notifications.repository import (
    NotificationRepository,
    NotificationRow,
    get_notification_repository,
)
from adminprop.shared.notifications.unread_cache import (
    get_cached_unread_count,
    invalidate_unread_count,
    set_cached_unread_count,
)


class NotificationService:
    def __init__(self, repo: NotificationRepository) -> None:
        self._repo = repo

    async def list(
        self, *, organization_id: UUID, user_id: UUID, unread_only: bool
    ) -> tuple[list[NotificationRow], int]:
        """RF-02: `GET /notifications` (`?unread=true`) + el contador de
        no leidas para el badge (mismo request, `meta.unread_count`)."""
        rows = await self._repo.list_by_user(
            organization_id=organization_id, user_id=user_id, unread_only=unread_only
        )
        unread_count = await self.unread_count(organization_id=organization_id, user_id=user_id)
        return rows, unread_count

    async def unread_count(self, *, organization_id: UUID, user_id: UUID) -> int:
        """CA-NT-04: badge -- cacheado 5 min (sdd_04 §1.4), recalculado
        desde Postgres en cache miss."""
        cached = await get_cached_unread_count(organization_id, user_id)
        if cached is not None:
            return cached
        count = await self._repo.count_unread(organization_id=organization_id, user_id=user_id)
        await set_cached_unread_count(organization_id, user_id, count)
        return count

    async def mark_read(
        self, notification_id: UUID, *, organization_id: UUID, user_id: UUID
    ) -> NotificationRow:
        """RF-02: `POST /notifications/:id/read`. RN-D01: no existe / de
        otro usuario / de otra organizacion -> 404 (el repository no
        distingue los tres casos)."""
        found = await self._repo.mark_read(
            notification_id=notification_id, organization_id=organization_id, user_id=user_id
        )
        if not found:
            raise NotFoundException()
        await self._repo.commit()
        # sdd_04 §1.4 "Invalidacion: al crear/leer notificacion".
        await invalidate_unread_count(organization_id, user_id)

        rows = await self._repo.list_by_user(
            organization_id=organization_id, user_id=user_id, unread_only=False
        )
        for row in rows:
            if row.id == notification_id:
                return row
        raise NotFoundException()  # pragma: no cover -- defensivo, ya validado arriba

    async def mark_all_read(self, *, organization_id: UUID, user_id: UUID) -> int:
        """RF-02: `POST /notifications/read-all` -- CA-NT-04: "el badge
        queda en cero". Devuelve cuantas se marcaron."""
        marked = await self._repo.mark_all_read(organization_id=organization_id, user_id=user_id)
        await self._repo.commit()
        await invalidate_unread_count(organization_id, user_id)
        return marked


def get_notification_service(
    repo: NotificationRepository = Depends(get_notification_repository),
) -> NotificationService:
    return NotificationService(repo)
