"""Endpoints /v1/notifications/* -- panel in-app (issue #31).

SDD: core/sdd_03_api_contracts.md §13 "Notificaciones" +
     infrastructure/spec_notificaciones.md RF-02.
Implements: CA-NT-01 (lectura de lo emitido por
`shared/notifications/service.emit`), CA-NT-04.

Permiso `notification:read` (owner/admin/maintenance -- sdd_03
§"Resumen de Autorizacion por Recurso" fila "Notificaciones propias").
`user_id` sale del JWT (`payload.sub`), nunca del path/query -- una
notificacion es siempre "propia" del usuario autenticado (RF-02: "GET
/notifications (propias del usuario)").
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from adminprop.modules.notifications.schemas import (
    Notification,
    NotificationListResponse,
    NotificationReadAllResponse,
    NotificationReadResponse,
)
from adminprop.modules.notifications.service import (
    NotificationService,
    get_notification_service,
)
from adminprop.shared.auth.jwt import JWTPayload
from adminprop.shared.rbac import requires_permission
from adminprop.shared.tenant import get_current_tenant

router = APIRouter(prefix="/v1/notifications", tags=["notifications"])


@router.get(
    "",
    response_model=NotificationListResponse,
    dependencies=[Depends(requires_permission("notification:read"))],
)
async def list_notifications(
    unread: bool = Query(default=False),
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("notification:read")),
    service: NotificationService = Depends(get_notification_service),
) -> NotificationListResponse:
    """RF-02: `GET /notifications` (`?unread=true`) -- solo las propias
    del usuario autenticado. `meta.unread_count` es el badge (CA-NT-04),
    cacheado 5 min (sdd_04 §1.4)."""
    rows, unread_count = await service.list(
        organization_id=organization_id, user_id=payload.sub, unread_only=unread
    )
    return NotificationListResponse(
        data=[Notification.model_validate(row) for row in rows],
        meta={"unread_count": unread_count},
    )


@router.post(
    "/{notification_id}/read",
    response_model=NotificationReadResponse,
    dependencies=[Depends(requires_permission("notification:read"))],
)
async def mark_notification_read(
    notification_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("notification:read")),
    service: NotificationService = Depends(get_notification_service),
) -> NotificationReadResponse:
    """RF-02: `POST /notifications/:id/read`. RN-D01: de otro usuario o
    de otra organizacion -> 404 (no revela existencia)."""
    row = await service.mark_read(
        notification_id, organization_id=organization_id, user_id=payload.sub
    )
    return NotificationReadResponse(data=Notification.model_validate(row))


@router.post(
    "/read-all",
    response_model=NotificationReadAllResponse,
    dependencies=[Depends(requires_permission("notification:read"))],
)
async def mark_all_notifications_read(
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("notification:read")),
    service: NotificationService = Depends(get_notification_service),
) -> NotificationReadAllResponse:
    """CA-NT-04: "read-all las marca todas y el badge queda en cero"."""
    marked = await service.mark_all_read(organization_id=organization_id, user_id=payload.sub)
    return NotificationReadAllResponse(data={"marked": marked})
