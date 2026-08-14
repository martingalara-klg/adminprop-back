"""tests/unit/modules/superadmin/test_repository_helpers.py

Helpers puros de repository.py (sin I/O): parseo de `settings` (JSONB
leido via SQL crudo puede llegar como dict o como str) y el cursor de
paginacion (sdd_03 §"Paginacion").
"""

import uuid
from datetime import UTC, datetime

from adminprop.modules.superadmin.repository import (
    _decode_cursor,
    _encode_cursor,
    _parse_settings,
)


class TestParseSettings:
    def test_returns_dict_as_is(self):
        assert _parse_settings({"grace_day": 10}) == {"grace_day": 10}

    def test_parses_json_string(self):
        assert _parse_settings('{"grace_day": 10}') == {"grace_day": 10}

    def test_returns_empty_dict_for_unexpected_type(self):
        assert _parse_settings(None) == {}
        assert _parse_settings(123) == {}


class TestCursorRoundTrip:
    def test_encode_then_decode_recovers_original_values(self):
        created_at = datetime(2026, 1, 15, 12, 30, tzinfo=UTC)
        organization_id = uuid.uuid4()

        cursor = _encode_cursor(created_at, organization_id)
        decoded_created_at, decoded_id = _decode_cursor(cursor)

        assert decoded_created_at == created_at
        assert decoded_id == organization_id
