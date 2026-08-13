"""Unit tests de helpers puros del repository de auth (issue #6)."""

from adminprop.modules.auth.repository import _parse_permissions


class TestParsePermissions:
    def test_returns_list_of_str_when_input_is_already_a_list(self):
        assert _parse_permissions(["contract:read", "contract:manage"]) == [
            "contract:read",
            "contract:manage",
        ]

    def test_parses_json_string_into_list(self):
        assert _parse_permissions('["contract:read"]') == ["contract:read"]

    def test_returns_empty_list_for_unexpected_type(self):
        assert _parse_permissions(None) == []
        assert _parse_permissions(42) == []
