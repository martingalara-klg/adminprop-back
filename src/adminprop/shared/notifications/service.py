"""NotificationService transversal (issue #11, extendido en el #31).

SDD: infrastructure/spec_notificaciones.md v1.1 RF-01 (emisión), RF-04
     (retry de email) + core/sdd_02_domain_model.md §2.16 "Notification".
Implements: RN-01 (enrutamiento por rol), RN-02 (una fila por
            destinatario), CA-NT-01, CA-NT-02, CA-NT-05.

Issue #31: agrega `quote_approved` -> `maintenance` a la tabla de
enrutamiento (decision #115, sexto evento del MVP -- requiere la
migracion `20260821_100000_add_quote_approved_to_notifications.py` que
extiende el CHECK de `notifications.event_type`) e invalida el cache del
badge de no leidas (`unread_cache.py`, `sdd_04` §1.4: "Invalidacion: al
crear/leer notificacion") de cada destinatario recien notificado.

API funcional (no clase con estado), mismo criterio que
`shared/audit/service.py`: `emit()` recibe la MISMA `session` que la
operación de negocio del caller y NO hace commit -- si esa operación
rollbackea, las notificaciones in-app también (CA-NT-02, "si el alta del
pedido de reparación falla a mitad de transacción, no queda ninguna
notificación creada"). El envío de email es responsabilidad separada:
el caller, DESPUÉS de confirmar su propio `commit()`, llama a
`enqueue_pending_emails()` para encolar el envío outbox (RF-01 "patrón
outbox simple": el worker toma notificaciones con `email_sent_at IS
NULL"). Mismo patrón de dos pasos que
`modules/administracion/service.py.UserService.invite` (repo.commit()
seguido de `send_transactional_email.delay(...)`).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from adminprop.shared.notifications.repository import NotificationRepository
from adminprop.shared.notifications.unread_cache import invalidate_unread_count

# RN-01 (spec_notificaciones.md v1.1 "Eventos del MVP y enrutamiento por
# rol"): agregar un evento nuevo = actualizar esta tabla primero (regla
# de oro de sdd_03, citada también en el SDD de notificaciones).
EVENT_RECIPIENT_ROLES: dict[str, tuple[str, ...]] = {
    "adjustment_pending": ("owner", "admin"),
    "contract_expiring": ("owner", "admin"),
    "work_order_created": ("maintenance",),
    "quote_submitted": ("owner", "admin"),
    "quote_approved": ("maintenance",),
    "work_order_closed": ("owner", "admin"),
}


async def emit(
    session: AsyncSession,
    *,
    organization_id: UUID,
    event_type: str,
    payload: dict,
) -> list[UUID]:
    """RF-01: crea la fila in-app de cada destinatario en la MISMA
    transacción de `session` -- sin commit propio (CA-NT-02).

    Devuelve los IDs de las notificaciones creadas; el caller los usa
    DESPUÉS de su propio `commit()` para encolar el email
    (`enqueue_pending_emails`).
    """
    if event_type not in EVENT_RECIPIENT_ROLES:
        raise ValueError(f"event_type desconocido: {event_type!r}")

    repo = NotificationRepository(session)
    # RN-01/CA-NT-05: solo miembros ACTIVOS de los roles de la tabla de
    # enrutamiento -- un usuario `inactive` no recibe avisos nuevos.
    recipients = await repo.list_active_recipients(
        organization_id=organization_id,
        role_names=EVENT_RECIPIENT_ROLES[event_type],
    )

    notification_ids: list[UUID] = []
    for recipient in recipients:
        # RN-02: un evento = una notificación por destinatario.
        notification_id = await repo.insert(
            organization_id=organization_id,
            user_id=recipient.user_id,
            event_type=event_type,
            payload=payload,
        )
        notification_ids.append(notification_id)
        # sdd_04 §1.4 "Badge de notificaciones no leidas | 5 min |
        # Al crear/leer notificacion": invalida el cache del destinatario
        # recien notificado (issue #31).
        await invalidate_unread_count(organization_id, recipient.user_id)
    return notification_ids


def enqueue_pending_emails(
    notification_ids: list[UUID], *, organization_id: UUID, request_id: str
) -> None:
    """RF-01: encola el envío de email DESPUÉS del commit del caller --
    patrón outbox simple (`email_sent_at IS NULL`).

    Import diferido de `adminprop.workers.notification_worker`: evita un
    ciclo de import si algún módulo de `workers/` llegara a necesitar
    `shared.notifications` en el futuro (mismo criterio defensivo que
    `shared/audit/service.py.record_access_denied`); hoy no hay ciclo
    real, pero es el patrón ya establecido en este repo para
    `shared/*` <-> `workers/*`.
    """
    from adminprop.workers.notification_worker import send_notification_email

    for notification_id in notification_ids:
        send_notification_email.apply_async(
            args=[str(notification_id), str(organization_id), request_id]
        )
