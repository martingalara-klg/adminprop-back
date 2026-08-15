"""tests/unit/shared/test_notifications_repository.py

Cobertura unitaria de los helpers puros / triviales de
`shared/notifications/repository.py` que no requieren Postgres real (la
cobertura de las queries SQL vive en tests/integration/notifications y
tests/integration/workers/test_notification_worker_outbox.py).
"""

from __future__ import annotations

from adminprop.shared.notifications.repository import NotificationRepository, _parse_payload


class TestParsePayload:
    def test_dict_is_returned_as_is(self):
        assert _parse_payload({"work_order_id": "abc"}) == {"work_order_id": "abc"}

    def test_json_string_is_parsed(self):
        """asyncpg puede devolver JSONB como texto crudo segun la query --
        `_parse_payload` normaliza ambos casos."""
        assert _parse_payload('{"work_order_id": "abc"}') == {"work_order_id": "abc"}

    def test_unexpected_type_falls_back_to_empty_dict(self):
        assert _parse_payload(None) == {}
        assert _parse_payload(123) == {}


class TestNotificationRepositorySessionProperty:
    def test_session_property_exposes_the_constructor_session(self):
        fake_session = object()
        repo = NotificationRepository(fake_session)  # type: ignore[arg-type]
        assert repo.session is fake_session
