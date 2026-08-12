"""Issue #4 — documents_worker: esqueleto que aplica el patron de tenant
context sin tocar Postgres real (esta suite se mockea; la verificacion
end-to-end contra Postgres real vive en
tests/integration/workers/test_documents_worker.py).

Skill: docs/skills/async-worker.md, docs/skills/tenant-isolation.md.
"""

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from adminprop.workers import documents_worker


@pytest.mark.asyncio
async def test_generate_document_skeleton_async_applies_tenant_scoped_session():
    """docs/skills/tenant-isolation.md: antes de cualquier query (aunque
    hoy no haya ninguna real) el esqueleto abre la sesion via
    `tenant_scoped_session(organization_id)` — el mismo primitivo que
    `set_tenant_context` usa en runtime (issue #3)."""
    captured = {}

    @asynccontextmanager
    async def fake_tenant_scoped_session(organization_id):
        captured["organization_id"] = organization_id
        yield AsyncMock()

    original = documents_worker.tenant_scoped_session
    documents_worker.tenant_scoped_session = fake_tenant_scoped_session
    try:
        org_id = uuid.uuid4()
        document_id = uuid.uuid4()
        await documents_worker._generate_document_skeleton_async(document_id, org_id, "req-1")
    finally:
        documents_worker.tenant_scoped_session = original

    assert captured["organization_id"] == org_id


def test_generate_document_skeleton_task_runs_without_raising(monkeypatch):
    """La tarea Celery delega en el helper async via `asyncio.run` (patron
    documentado en docs/skills/async-worker.md) y no genera ningun archivo."""
    called = {}

    async def fake_async(document_id, organization_id, request_id):
        called["args"] = (document_id, organization_id, request_id)

    monkeypatch.setattr(documents_worker, "_generate_document_skeleton_async", fake_async)

    document_id = str(uuid.uuid4())
    organization_id = str(uuid.uuid4())
    documents_worker.generate_document_skeleton.apply(
        args=[document_id, organization_id, "req-2"]
    ).get()

    assert called["args"] == (uuid.UUID(document_id), uuid.UUID(organization_id), "req-2")
