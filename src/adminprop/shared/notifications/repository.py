"""Acceso a datos de `notifications` (issue #11).

SDD: infrastructure/spec_data_model.md §Capa 7 "notifications".

Mismo criterio que `shared/audit/repository.py` y
`modules/administracion/repository.py`: SQL crudo via `text()` -- esta
tabla todavía no tiene un dueño ORM (el panel in-app del issue #31 podría
introducir un modelo SQLAlchemy si necesita queries más ricas).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

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
