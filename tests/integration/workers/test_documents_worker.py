"""Issue #4 — documents_worker: el esqueleto aplica `tenant_scoped_session`
contra Postgres real (no solo mockeado — ver tests/unit/workers/
test_documents_worker.py para la version rapida sin DB).

Skill: docs/skills/tenant-isolation.md, docs/skills/async-worker.md.
Requiere Postgres real (mismo patron que tests/integration/db — ver su
docstring): `docker/docker-compose.yml` local o el servicio `postgres` de
`.github/workflows/ci.yml`, con `alembic upgrade head` ya corrido.
"""

import uuid

import pytest

from adminprop.workers.documents_worker import _generate_document_skeleton_async

pytestmark = pytest.mark.asyncio


async def test_generate_document_skeleton_async_runs_against_real_postgres():
    """El esqueleto abre una sesion real, setea el tenant context (RN-D01)
    y termina sin excepcion — sin tabla de negocio que consultar todavia
    (llega con los issues de Liquidaciones), pero probando que la
    conexion + `tenant_scoped_session` funcionan end-to-end."""
    org_id = uuid.uuid4()
    document_id = uuid.uuid4()

    await _generate_document_skeleton_async(document_id, org_id, "req-integration-1")
