"""tests/unit/shared/test_audit_service.py

SDD: core/sdd_02_domain_model.md §2.17 "Log de Auditoria (AuditLog)"
     + core/sdd_04_nonfunctional.md §2.4 (scrubbing de campos sensibles).
Implements: CA-10-02 (AuditService usable por todos los modulos),
            RN-D04 (before/after correctamente persistidos).

Unit test de `audit()` con una sesion fake -- no requiere Postgres real
(la cobertura de la escritura real en `audit_logs` vive en
`tests/integration/db/test_audit_logs.py` y
`tests/integration/shared/test_access_denied_audit.py`).
"""

from __future__ import annotations

import uuid

import pytest

from adminprop.shared.audit.service import audit, record_access_denied
from adminprop.shared.logging.json_logger import request_id_var

pytestmark = pytest.mark.asyncio


class _FakeSession:
    """Captura los `execute(stmt, params)` sin tocar Postgres."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, stmt, params=None):
        self.calls.append(dict(params or {}))


class TestAuditFunction:
    """CA-10-02: `audit()` es la interfaz comun que cualquier modulo usa."""

    async def test_ca_10_02_inserts_with_the_given_session_no_commit(self):
        """`audit()` NUNCA comitea -- queda en la transaccion del caller
        (docstring del modulo: 'si esa operacion hace rollback, el INSERT
        del audit tambien')."""
        session = _FakeSession()
        organization_id = uuid.uuid4()
        entity_id = uuid.uuid4()
        user_id = uuid.uuid4()

        await audit(
            session,
            organization_id=organization_id,
            action="user.role_changed",
            entity_type="organization_member",
            entity_id=entity_id,
            before={"role": "admin"},
            after={"role": "maintenance"},
            user_id=user_id,
            request_id="req-123",
        )

        assert not hasattr(session, "commit_called")
        assert len(session.calls) == 1
        call = session.calls[0]
        assert call["organization_id"] == str(organization_id)
        assert call["user_id"] == str(user_id)
        assert call["action"] == "user.role_changed"
        assert call["entity_type"] == "organization_member"
        assert call["entity_id"] == str(entity_id)
        assert call["before_state"] == {"role": "admin"}
        assert call["after_state"] == {"role": "maintenance"}
        assert call["request_id"] == "req-123"

    async def test_ca_10_02_user_id_none_for_system_actions(self):
        """sdd_02 §2.17: `user_id` NULL para acciones del sistema."""
        session = _FakeSession()

        await audit(
            session,
            organization_id=uuid.uuid4(),
            action="adjustment.applied",
            entity_type="contract_adjustment",
        )

        assert session.calls[0]["user_id"] is None
        assert session.calls[0]["entity_id"] is None
        assert session.calls[0]["before_state"] is None
        assert session.calls[0]["after_state"] is None

    async def test_scrubs_sensitive_keys_from_before_and_after(self):
        """sdd_04 §2.4: password_hash/tokens/bank_info nunca se persisten,
        ni siquiera dentro de `before`/`after` (reusa
        shared/logging/json_logger.scrub, mismas SENSITIVE_KEYS)."""
        session = _FakeSession()

        await audit(
            session,
            organization_id=uuid.uuid4(),
            action="settings.changed",
            entity_type="organization",
            before={"password_hash": "old-hash", "grace_day": 10},
            after={"bank_info": {"cbu": "123"}, "grace_day": 20},
        )

        call = session.calls[0]
        assert call["before_state"] == {"password_hash": "[REDACTED]", "grace_day": 10}
        assert call["after_state"] == {"bank_info": "[REDACTED]", "grace_day": 20}

    async def test_request_id_falls_back_to_contextvar_when_not_provided(self):
        """`request_id` viene del contextvar de shared/logging si no se
        pasa explicito (RequestContextMiddleware lo setea en cada request)."""
        session = _FakeSession()
        token = request_id_var.set("ctx-request-id")
        try:
            await audit(
                session,
                organization_id=uuid.uuid4(),
                action="access.denied",
                entity_type="access",
            )
        finally:
            request_id_var.reset(token)

        assert session.calls[0]["request_id"] == "ctx-request-id"

    async def test_explicit_request_id_overrides_contextvar(self):
        session = _FakeSession()
        token = request_id_var.set("ctx-request-id")
        try:
            await audit(
                session,
                organization_id=uuid.uuid4(),
                action="access.denied",
                entity_type="access",
                request_id="explicit-id",
            )
        finally:
            request_id_var.reset(token)

        assert session.calls[0]["request_id"] == "explicit-id"


class TestRecordAccessDenied:
    """RN-A04, caso limite: sin `organization_id` no hay tenant al que
    atribuir la fila (JWT de Super Admin, sin `org`, llegando a una
    dependency tenant-scoped) -- se omite en silencio en vez de romper el
    request con un error de FK/NOT NULL."""

    async def test_skips_silently_when_organization_id_is_none(self):
        # No abre conexion real: si `organization_id` fuera tratado como
        # valido, esto fallaria por falta de una sesion/engine real.
        result = await record_access_denied(
            organization_id=None,
            user_id=uuid.uuid4(),
            details={"permission": "user:manage"},
        )
        assert result is None
