"""tests/unit/shared/test_notifications_service.py

SDD: infrastructure/spec_notificaciones.md RF-01, RN-01.
Implements: cobertura unitaria de `shared/notifications/service.py`
sin tocar Postgres (la cobertura de `emit()` contra datos reales vive en
tests/integration/notifications/test_emit_and_outbox.py).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from adminprop.shared.notifications.service import (
    EVENT_RECIPIENT_ROLES,
    emit,
    enqueue_pending_emails,
)


class TestEventRecipientRoutingTable:
    """RN-01: la tabla de enrutamiento cubre exactamente los 6 eventos
    del MVP (spec_notificaciones.md v1.1 "Eventos del MVP y enrutamiento
    por rol" -- issue #31 agrego `quote_approved`, decision #115)."""

    def test_covers_exactly_the_6_mvp_events(self):
        assert set(EVENT_RECIPIENT_ROLES) == {
            "adjustment_pending",
            "contract_expiring",
            "quote_submitted",
            "quote_approved",
            "work_order_created",
            "work_order_closed",
        }

    @pytest.mark.parametrize("event_type", ["work_order_created", "quote_approved"])
    def test_maintenance_only_events_route_to_maintenance_only(self, event_type):
        assert EVENT_RECIPIENT_ROLES[event_type] == ("maintenance",)

    @pytest.mark.parametrize(
        "event_type",
        ["adjustment_pending", "contract_expiring", "quote_submitted", "work_order_closed"],
    )
    def test_owner_and_admin_events_route_to_both_roles(self, event_type):
        assert EVENT_RECIPIENT_ROLES[event_type] == ("owner", "admin")


class TestEmitRejectsUnknownEventTypeWithoutTouchingDb:
    @pytest.mark.asyncio
    async def test_raises_value_error_before_any_query(self):
        """La validacion de `event_type` ocurre ANTES de construir el
        repository -- un `session=None` nunca se toca si el evento no
        existe en el catalogo."""
        with pytest.raises(ValueError, match="event_type desconocido"):
            await emit(
                None,  # type: ignore[arg-type]
                organization_id=uuid.uuid4(),
                event_type="not_a_real_event",
                payload={},
            )


class TestEnqueuePendingEmails:
    """RF-01: encola una tarea Celery por notificacion, con los args
    correctos (IDs como string, docs/skills/async-worker.md)."""

    def test_enqueues_one_task_per_notification_id(self, monkeypatch):
        mock_task = MagicMock()
        monkeypatch.setattr(
            "adminprop.workers.notification_worker.send_notification_email", mock_task
        )
        notification_ids = [uuid.uuid4(), uuid.uuid4()]
        organization_id = uuid.uuid4()

        enqueue_pending_emails(
            notification_ids, organization_id=organization_id, request_id="req-x"
        )

        assert mock_task.apply_async.call_count == 2
        mock_task.apply_async.assert_any_call(
            args=[str(notification_ids[0]), str(organization_id), "req-x"]
        )
        mock_task.apply_async.assert_any_call(
            args=[str(notification_ids[1]), str(organization_id), "req-x"]
        )

    def test_no_notification_ids_enqueues_nothing(self, monkeypatch):
        mock_task = MagicMock()
        monkeypatch.setattr(
            "adminprop.workers.notification_worker.send_notification_email", mock_task
        )

        enqueue_pending_emails([], organization_id=uuid.uuid4(), request_id="req-y")

        mock_task.apply_async.assert_not_called()
