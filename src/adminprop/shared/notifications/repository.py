"""Acceso a datos de `notifications` (issue #11, extendido en el #31).

SDD: infrastructure/spec_data_model.md §Capa 7 "notifications" +
     infrastructure/spec_notificaciones.md RF-02 (panel in-app).

Mismo criterio que `shared/audit/repository.py` y
`modules/administracion/repository.py`: SQL crudo via `text()` -- esta
tabla todavía no tiene un dueño ORM. El issue #31 agrega los metodos del
panel (`list_by_user`, `count_unread`, `mark_read`, `mark_all_read`,
`commit`) aca mismo, no en un repository nuevo de `modules/notifications/`,
para no duplicar el SQL de acceso a `notifications` -- ese modulo solo
aporta router+service+schemas (ver docs/skills/module-structure.md).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from adminprop.db.session import get_tenant_db_session

# `none_as_null=True`: mismo motivo que `shared/audit/repository.py` --
# sin esto, un `payload={}` se serializaria correctamente pero un valor
# Python `None` explicito quedaria como el literal JSON 'null' en vez de
# SQL NULL (no aplica hoy, `payload` es NOT NULL, pero mismo tipo de bind
# reutilizado por consistencia y para futuras columnas JSON nullable).
_JSON_PAYLOAD = sa.JSON(none_as_null=True)


@dataclass(frozen=True)
class RecipientRow:
    """Destinatario activo resuelto por rol (RN-01)."""

    user_id: UUID
    email: str


@dataclass(frozen=True)
class NotificationRow:
    """Fila propia del usuario para el panel in-app (RF-02, issue #31)."""

    id: UUID
    event_type: str
    payload: dict
    read_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class PendingNotificationRow:
    """Fila de `notifications` lockeada para envío de email (outbox)."""

    id: UUID
    organization_id: UUID
    event_type: str
    payload: dict
    recipient_email: str


def _parse_payload(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    return {}


class NotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Expuesto para que `service.emit()` reutilice la MISMA sesión
        que la operación de negocio del caller (mismo patrón que
        `modules/administracion/repository.py.session` y
        `shared/audit/service.py`)."""
        return self._session

    # ─── RN-01: resolución de destinatarios por rol ────────────────────

    async def list_active_recipients(
        self, *, organization_id: UUID, role_names: tuple[str, ...]
    ) -> list[RecipientRow]:
        """RN-01/CA-NT-05: solo membresías `active` (un usuario
        `inactive` no recibe avisos) con rol en `role_names`. Filtro
        explícito de `organization_id` (defense in depth sobre RLS,
        RN-D01) en ambas tablas del join."""
        stmt = text(
            """
            SELECT DISTINCT u.id AS user_id, u.email
            FROM organization_members m
            JOIN users u ON u.id = m.user_id AND u.deleted_at IS NULL
            JOIN roles r ON r.id = m.role_id AND r.organization_id = m.organization_id
            WHERE m.organization_id = :organization_id
              AND m.status = 'active'
              AND r.name = ANY(:role_names)
            """
        ).bindparams(sa.bindparam("role_names", type_=sa.ARRAY(sa.Text())))
        result = await self._session.execute(
            stmt,
            {"organization_id": str(organization_id), "role_names": list(role_names)},
        )
        return [RecipientRow(user_id=row.user_id, email=row.email) for row in result]

    # ─── RF-01: emisión (in-app, misma transacción) ────────────────────

    async def insert(
        self, *, organization_id: UUID, user_id: UUID, event_type: str, payload: dict
    ) -> UUID:
        """RN-02: una fila por destinatario. Sin commit propio -- vive en
        la transacción de `session` (CA-NT-02)."""
        stmt = text(
            """
            INSERT INTO notifications (organization_id, user_id, event_type, payload)
            VALUES (:organization_id, :user_id, :event_type, :payload)
            RETURNING id
            """
        ).bindparams(sa.bindparam("payload", type_=_JSON_PAYLOAD))
        result = await self._session.execute(
            stmt,
            {
                "organization_id": str(organization_id),
                "user_id": str(user_id),
                "event_type": event_type,
                "payload": payload,
            },
        )
        return result.scalar_one()

    # ─── RF-04: outbox de email ─────────────────────────────────────────

    async def lock_pending_email(
        self, *, notification_id: UUID, organization_id: UUID
    ) -> PendingNotificationRow | None:
        """RF-01 "patrón outbox simple" + idempotencia del drenaje:
        `FOR UPDATE SKIP LOCKED` -- si otra corrida ya está procesando
        (o ya envió) esta notificación, retorna `None` en vez de
        bloquear o duplicar el envío. `email_sent_at IS NULL` filtra las
        ya enviadas."""
        stmt = text(
            """
            SELECT n.id, n.organization_id, n.event_type, n.payload, u.email AS recipient_email
            FROM notifications n
            JOIN users u ON u.id = n.user_id
            WHERE n.id = :id
              AND n.organization_id = :organization_id
              AND n.email_sent_at IS NULL
            FOR UPDATE OF n SKIP LOCKED
            """
        )
        result = await self._session.execute(
            stmt, {"id": str(notification_id), "organization_id": str(organization_id)}
        )
        row = result.mappings().first()
        if row is None:
            return None
        return PendingNotificationRow(
            id=row["id"],
            organization_id=row["organization_id"],
            event_type=row["event_type"],
            payload=_parse_payload(row["payload"]),
            recipient_email=row["recipient_email"],
        )

    async def mark_email_sent(self, notification_id: UUID) -> None:
        stmt = text("UPDATE notifications SET email_sent_at = now() WHERE id = :id")
        await self._session.execute(stmt, {"id": str(notification_id)})

    async def get_organization_email_context(
        self, organization_id: UUID
    ) -> tuple[str, str | None] | None:
        """Nombre de la organización + email del owner activo (Reply-To,
        spec_notificaciones.md §Email: "From dinámico ... Reply-To: el
        email del owner de la organización"). `None` si la organización
        no existe (defensivo -- no debería pasar, `organization_id` viene
        de una notificación ya insertada con esa FK)."""
        stmt = text(
            """
            SELECT o.name,
                   (
                       SELECT u.email
                       FROM organization_members m
                       JOIN roles r ON r.id = m.role_id AND r.organization_id = m.organization_id
                       JOIN users u ON u.id = m.user_id
                       WHERE m.organization_id = o.id
                         AND m.status = 'active'
                         AND r.name = 'owner'
                       ORDER BY m.created_at
                       LIMIT 1
                   ) AS owner_email
            FROM organizations o
            WHERE o.id = :organization_id AND o.deleted_at IS NULL
            """
        )
        result = await self._session.execute(stmt, {"organization_id": str(organization_id)})
        row = result.first()
        if row is None:  # pragma: no cover -- defensivo
            return None
        return row.name, row.owner_email

    # ─── RF-02: panel in-app (issue #31) ───────────────────────────────

    async def list_by_user(
        self, *, organization_id: UUID, user_id: UUID, unread_only: bool
    ) -> list[NotificationRow]:
        """RF-02: `GET /notifications` propias del usuario, mas recientes
        primero. `?unread=true` filtra `read_at IS NULL`. Filtro EXPLICITO
        de `organization_id` + `user_id` (RN-D01, defense in depth) --
        nunca solo `user_id` (un `user_id` filtrado sin `organization_id`
        bastaria por unicidad de PK, pero el filtro explicito es el
        criterio del repo, ver docs/skills/tenant-isolation.md)."""
        stmt = text(
            f"""
            SELECT id, event_type, payload, read_at, created_at
            FROM notifications
            WHERE organization_id = :organization_id
              AND user_id = :user_id
              {"AND read_at IS NULL" if unread_only else ""}
            ORDER BY created_at DESC
            """
        )
        result = await self._session.execute(
            stmt, {"organization_id": str(organization_id), "user_id": str(user_id)}
        )
        return [
            NotificationRow(
                id=row.id,
                event_type=row.event_type,
                payload=_parse_payload(row.payload),
                read_at=row.read_at,
                created_at=row.created_at,
            )
            for row in result
        ]

    async def count_unread(self, *, organization_id: UUID, user_id: UUID) -> int:
        """RF-02: badge -- cantidad de no leidas del usuario (cacheado 5
        min por `shared/notifications/unread_cache.py`, sdd_04 §1.4)."""
        stmt = text(
            """
            SELECT count(*) FROM notifications
            WHERE organization_id = :organization_id
              AND user_id = :user_id
              AND read_at IS NULL
            """
        )
        result = await self._session.execute(
            stmt, {"organization_id": str(organization_id), "user_id": str(user_id)}
        )
        return result.scalar_one()

    async def mark_read(
        self, *, notification_id: UUID, organization_id: UUID, user_id: UUID
    ) -> bool:
        """RF-02: `POST /notifications/:id/read`. Filtro explicito por
        `user_id` ademas de `organization_id`: una notificacion es propia
        de UN destinatario -- un usuario no puede marcar como leida la de
        otro miembro de la misma organizacion (RN-D01 aplicado tambien a
        nivel de fila propia, no solo de tenant). Devuelve `False` si no
        existe / es de otro usuario / de otra organizacion -- el service
        mapea eso a 404 (no distingue los tres casos, mismo criterio
        cross-tenant de `docs/skills/tenant-isolation.md`)."""
        stmt = text(
            """
            UPDATE notifications SET read_at = now()
            WHERE id = :id AND organization_id = :organization_id AND user_id = :user_id
              AND read_at IS NULL
            RETURNING id
            """
        )
        result = await self._session.execute(
            stmt,
            {
                "id": str(notification_id),
                "organization_id": str(organization_id),
                "user_id": str(user_id),
            },
        )
        return result.first() is not None or await self._exists_already_read(
            notification_id, organization_id, user_id
        )

    async def _exists_already_read(
        self, notification_id: UUID, organization_id: UUID, user_id: UUID
    ) -> bool:
        """Idempotencia de `mark_read`: si la fila existe pero ya estaba
        leida, el UPDATE de arriba no la toca (WHERE read_at IS NULL) --
        sin este chequeo, marcar dos veces la misma notificacion
        retornaria 404 en la segunda llamada, un falso negativo."""
        stmt = text(
            "SELECT 1 FROM notifications "
            "WHERE id = :id AND organization_id = :organization_id AND user_id = :user_id"
        )
        result = await self._session.execute(
            stmt,
            {
                "id": str(notification_id),
                "organization_id": str(organization_id),
                "user_id": str(user_id),
            },
        )
        return result.first() is not None

    async def mark_all_read(self, *, organization_id: UUID, user_id: UUID) -> int:
        """RF-02: `POST /notifications/read-all` -- marca TODAS las no
        leidas del usuario, devuelve cuantas se marcaron (CA-NT-04: "el
        badge queda en cero")."""
        stmt = text(
            """
            UPDATE notifications SET read_at = now()
            WHERE organization_id = :organization_id AND user_id = :user_id
              AND read_at IS NULL
            RETURNING id
            """
        )
        result = await self._session.execute(
            stmt, {"organization_id": str(organization_id), "user_id": str(user_id)}
        )
        return len(result.fetchall())

    async def commit(self) -> None:
        await self._session.commit()


def get_notification_repository(
    session: AsyncSession = Depends(get_tenant_db_session),
) -> NotificationRepository:
    """DI del router de `modules/notifications/` (issue #31) -- mismo
    patron que `modules/maintenance/repository.py.get_work_order_repository`."""
    return NotificationRepository(session)
