"""Issue #11 — send_notification_email: cuerpo async real contra Postgres.

Requiere Postgres real con `alembic upgrade head` ya corrido -- mismo
patron que `tests/integration/workers/test_documents_worker.py`. Se
invoca `_send_notification_email_async` directamente (no el wrapper
Celery sincronico, que hace `asyncio.run()` y no puede llamarse desde un
test ya corriendo dentro del loop de pytest-asyncio) -- la politica de
reintentos del wrapper esta cubierta, mockeada, en
tests/unit/workers/test_notification_worker.py.

SDD: infrastructure/spec_notificaciones.md RF-01/RF-04.
Implements: CA-NT-03.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_engine, get_session_factory, set_tenant_context
from adminprop.shared.cache.redis import get_redis_client
from adminprop.shared.errors.retryable import RetryableNotificationError
from adminprop.shared.notifications.service import emit
from adminprop.workers import notification_worker
from adminprop.workers.notification_worker import _send_notification_email_async

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _fresh_engine_per_test() -> AsyncGenerator[None]:
    get_session_factory.cache_clear()
    get_engine.cache_clear()
    # Issue #31: `emit()` ahora invalida el cache del badge de no leidas
    # (`shared/notifications/unread_cache.py`) via `get_redis_client()` --
    # mismo motivo que el resto de los conftest de integration (evita
    # "Event loop is closed" al reusar un cliente Redis cacheado de un
    # test anterior con su propio loop de pytest-asyncio ya cerrado).
    get_redis_client.cache_clear()
    yield
    engine = get_engine()
    await engine.dispose()
    get_session_factory.cache_clear()
    get_engine.cache_clear()
    redis = get_redis_client()
    await redis.flushdb()
    await redis.aclose()
    get_redis_client.cache_clear()


async def _seed_org_with_owner_and_notification() -> tuple[uuid.UUID, uuid.UUID]:
    """Siembra una organizacion con un owner activo y emite un evento
    real (`quote_submitted`) -- devuelve `(organization_id,
    notification_id)` de la unica fila creada."""
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    role_id = uuid.uuid4()
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        await session.execute(
            sa.text("INSERT INTO organizations (id, slug, name) VALUES (:id, :slug, :name)"),
            {"id": str(org_id), "slug": f"org-{org_id.hex[:8]}", "name": "Org outbox"},
        )
        await session.execute(
            sa.text(
                "INSERT INTO users (id, email, password_hash, full_name, is_super_admin) "
                "VALUES (:id, :email, 'x', 'Owner', FALSE)"
            ),
            {"id": str(user_id), "email": f"owner-{user_id.hex[:8]}@example.com"},
        )
        await session.execute(
            sa.text(
                "INSERT INTO roles (id, organization_id, name, permissions) "
                "VALUES (:id, :org_id, 'owner', :permissions)"
            ).bindparams(sa.bindparam("permissions", type_=sa.JSON)),
            {"id": str(role_id), "org_id": str(org_id), "permissions": json.dumps([])},
        )
        await session.execute(
            sa.text(
                "INSERT INTO organization_members (organization_id, user_id, role_id, status) "
                "VALUES (:org_id, :user_id, :role_id, 'active')"
            ),
            {"org_id": str(org_id), "user_id": str(user_id), "role_id": str(role_id)},
        )

    async with session_factory() as session, session.begin():
        await set_tenant_context(session, org_id)
        notification_ids = await emit(
            session,
            organization_id=org_id,
            event_type="quote_submitted",
            payload={"work_order_id": str(uuid.uuid4())},
        )
    return org_id, notification_ids[0]


async def _email_sent_at(organization_id: uuid.UUID, notification_id: uuid.UUID):
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        result = await session.execute(
            sa.text("SELECT email_sent_at FROM notifications WHERE id = :id"),
            {"id": str(notification_id)},
        )
        return result.scalar_one()


class TestSendNotificationEmailAsyncSuccess:
    async def test_marks_email_sent_at_on_success(self, monkeypatch):
        org_id, notification_id = await _seed_org_with_owner_and_notification()
        mock_send = AsyncMock(return_value="msg-ok")
        monkeypatch.setattr(notification_worker, "send_email", mock_send)

        await _send_notification_email_async(notification_id, org_id, "req-outbox-1")

        assert mock_send.call_count == 1
        assert await _email_sent_at(org_id, notification_id) is not None


class TestCaNt03RetryableFailureLeavesRowUnsent:
    """CA-NT-03: "el aviso in-app existe, y el email se reintenta ...
    agotados los reintentos queda registrado el fallo con request_id"."""

    async def test_retryable_error_propagates_and_leaves_email_sent_at_null(self, monkeypatch):
        org_id, notification_id = await _seed_org_with_owner_and_notification()
        mock_send = AsyncMock(side_effect=RetryableNotificationError("resend 503"))
        monkeypatch.setattr(notification_worker, "send_email", mock_send)

        with pytest.raises(RetryableNotificationError):
            await _send_notification_email_async(notification_id, org_id, "req-outbox-2")

        # session.begin() hizo rollback automatico: mark_email_sent nunca
        # se llego a comitear -- la fila in-app sigue existiendo (no se
        # borro nada) pero el email sigue pendiente para el reintento.
        assert await _email_sent_at(org_id, notification_id) is None


class TestOutboxIdempotentUnderConcurrentDrain:
    """Idempotencia del drenaje (FOR UPDATE SKIP LOCKED): una notificacion
    ya enviada no se reenvia en una segunda corrida."""

    async def test_already_sent_notification_is_skipped_on_second_run(self, monkeypatch):
        org_id, notification_id = await _seed_org_with_owner_and_notification()
        mock_send = AsyncMock(return_value="msg-ok")
        monkeypatch.setattr(notification_worker, "send_email", mock_send)

        await _send_notification_email_async(notification_id, org_id, "req-outbox-3")
        await _send_notification_email_async(notification_id, org_id, "req-outbox-3-retry")

        assert mock_send.call_count == 1  # la segunda corrida no reenvia
