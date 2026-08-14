"""tests/unit/modules/administracion/test_repository_helpers.py

Helpers puros de repository.py (sin I/O): parseo de `permissions`/`settings`
(JSONB leido via SQL crudo puede llegar como list/dict o como str) y el
cursor de paginacion (sdd_03 §"Paginacion"). Mismo patron que
tests/unit/modules/superadmin/test_repository_helpers.py.
"""

import uuid
from datetime import UTC, datetime

from adminprop.modules.administracion.repository import (
    _decode_cursor,
    _encode_cursor,
    _parse_json_list,
    _parse_settings,
)


class TestParseJsonList:
    def test_returns_list_as_is(self):
        assert _parse_json_list(["user:manage", "role:read"]) == ["user:manage", "role:read"]

    def test_parses_json_string(self):
        assert _parse_json_list('["user:manage"]') == ["user:manage"]

    def test_returns_empty_list_for_unexpected_type(self):
        assert _parse_json_list(None) == []
        assert _parse_json_list(123) == []


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
        row_id = uuid.uuid4()

        cursor = _encode_cursor(created_at, row_id)
        decoded_created_at, decoded_id = _decode_cursor(cursor)

        assert decoded_created_at == created_at
        assert decoded_id == row_id
