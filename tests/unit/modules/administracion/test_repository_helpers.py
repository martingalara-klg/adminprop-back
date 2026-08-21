"""tests/unit/modules/administracion/test_repository_helpers.py

Helpers puros de repository.py (sin I/O): parseo de `permissions`/`settings`
(JSONB leido via SQL crudo puede llegar como list/dict o como str) y el
cursor de paginacion (sdd_03 §"Paginacion"). Mismo patron que
tests/unit/modules/superadmin/test_repository_helpers.py.
"""

import uuid
from datetime import UTC, datetime

import pytest

from adminprop.modules.administracion.audit_query_repository import AuditLogRow
from adminprop.modules.administracion.repository import (
    RoleRow,
    _decode_cursor,
    _encode_cursor,
    _parse_json_list,
    _parse_settings,
)
from adminprop.modules.administracion.router import _to_audit_log_entry
from adminprop.modules.administracion.service import RoleService
from adminprop.modules.superadmin.provisioning import OWNER_PERMISSIONS
from adminprop.shared.errors.codes import SystemRoleImmutableException


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


class TestCA0703SystemRoleImmutable:
    """CA-07-03 (spec_module_07_administracion.md): "Intentar editar un
    rol de sistema devuelve 422 SYSTEM_ROLE_IMMUTABLE". `sdd_03` §3 no
    define un endpoint de escritura de roles en MVP (`GET /roles` es
    solo lectura); este test cubre la invariante RN-03 a nivel de
    servicio, invocable por cualquier endpoint de escritura futuro."""

    def test_ca_07_03_system_role_immutable(self):
        role = RoleRow(
            id=uuid.uuid4(),
            name="owner",
            permissions=list(OWNER_PERMISSIONS),
            is_system_role=True,
        )

        with pytest.raises(SystemRoleImmutableException):
            RoleService.ensure_role_editable(role)

    def test_ensure_role_editable_allows_non_system_roles(self):
        """Defensivo: si en el futuro existieran roles custom
        (`is_system_role=False`, post-MVP segun RF-03), el metodo no
        levanta excepcion."""
        role = RoleRow(
            id=uuid.uuid4(),
            name="custom",
            permissions=["contract:read"],
            is_system_role=False,
        )

        RoleService.ensure_role_editable(role)


class TestToAuditLogEntry:
    """RF-05 (issue #32): mapeo puro de `AuditLogRow` (repository) a
    `AuditLogEntry` (schema del response)."""

    def test_maps_all_fields_including_before_after_state(self):
        row = AuditLogRow(
            id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            user_email="owner@example.com",
            action="user.role_changed",
            entity_type="organization_member",
            entity_id=uuid.uuid4(),
            before_state={"role": "admin"},
            after_state={"role": "maintenance"},
            request_id="req-123",
            created_at=datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
        )

        entry = _to_audit_log_entry(row)

        assert entry.id == row.id
        assert entry.user_id == row.user_id
        assert entry.action == "user.role_changed"
        assert entry.entity_type == "organization_member"
        assert entry.entity_id == row.entity_id
        assert entry.before_state == {"role": "admin"}
        assert entry.after_state == {"role": "maintenance"}
        assert entry.request_id == "req-123"

    def test_maps_null_user_and_entity_for_system_events(self):
        """`access.denied` y otros eventos de sistema pueden tener
        `user_id`/`entity_id`/`before_state` en None (sdd_02 §2.17)."""
        row = AuditLogRow(
            id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            user_id=None,
            user_email=None,
            action="access.denied",
            entity_type="access",
            entity_id=None,
            before_state=None,
            after_state={"permission": "user:manage"},
            request_id=None,
            created_at=datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
        )

        entry = _to_audit_log_entry(row)

        assert entry.user_id is None
        assert entry.entity_id is None
        assert entry.before_state is None
        assert entry.request_id is None
