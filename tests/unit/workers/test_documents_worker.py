"""Issue #29 -- documents_worker: `generate_settlement`, mockeado (sin
Postgres real; la verificacion end-to-end vive en
tests/integration/workers/test_documents_worker.py).

Reemplaza tests/unit/workers/test_documents_worker.py del issue #4, que
testeaba el esqueleto `generate_document_skeleton` -- su propio docstring
documentaba que #29/#30 reemplazarian el cuerpo.

Skill: docs/skills/async-worker.md, docs/skills/tenant-isolation.md.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from adminprop.modules.settlements.repository import GatheredSettlementData
from adminprop.shared.errors.retryable import NonRetryableError, RetryableError
from adminprop.workers import documents_worker


class _FakeSettlement:
    def __init__(self, landlord_id, period, commission_pct_used, exchange_rate=None):
        self.landlord_id = landlord_id
        self.period = period
        self.commission_pct_used = commission_pct_used
        self.exchange_rate = exchange_rate


@pytest.mark.asyncio
async def test_generate_settlement_async_applies_tenant_scoped_session(monkeypatch):
    """docs/skills/tenant-isolation.md: antes de cualquier query, el
    worker abre la sesion via `tenant_scoped_session(organization_id)`."""
    captured = {}

    @asynccontextmanager
    async def fake_tenant_scoped_session(organization_id):
        captured["organization_id"] = organization_id
        session = AsyncMock()
        yield session

    monkeypatch.setattr(documents_worker, "tenant_scoped_session", fake_tenant_scoped_session)

    settlement_id = uuid.uuid4()
    org_id = uuid.uuid4()
    landlord_id = uuid.uuid4()

    fake_repo = AsyncMock()
    fake_repo.get_by_id.return_value = _FakeSettlement(
        landlord_id=landlord_id, period="2026-06-01", commission_pct_used=Decimal("10.00")
    )
    fake_repo.gather_generation_data.return_value = GatheredSettlementData(
        payments=[], charge_entries=[], repairs=[]
    )
    fake_repo.list_unpaid_rent_periods.return_value = []
    fake_repo.list_missing_charge_entries.return_value = []
    fake_repo.apply_calculation = AsyncMock()

    monkeypatch.setattr(documents_worker, "SettlementRepository", lambda session: fake_repo)
    monkeypatch.setattr(documents_worker, "set_job_status", AsyncMock())

    await documents_worker._generate_settlement_async(settlement_id, org_id, "req-1")

    assert captured["organization_id"] == org_id
    fake_repo.apply_calculation.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_settlement_async_missing_settlement_is_a_noop(monkeypatch):
    """Liquidacion borrada entre encolar y procesar -- termina limpio."""

    @asynccontextmanager
    async def fake_tenant_scoped_session(organization_id):
        yield AsyncMock()

    monkeypatch.setattr(documents_worker, "tenant_scoped_session", fake_tenant_scoped_session)

    fake_repo = AsyncMock()
    fake_repo.get_by_id.return_value = None
    monkeypatch.setattr(documents_worker, "SettlementRepository", lambda session: fake_repo)

    set_job_status_mock = AsyncMock()
    monkeypatch.setattr(documents_worker, "set_job_status", set_job_status_mock)

    await documents_worker._generate_settlement_async(uuid.uuid4(), uuid.uuid4(), "req-2")

    fake_repo.apply_calculation.assert_not_called()
    # RF-01: "processing" se setea antes de chequear, pero no hay
    # completed/with_errors/failed cuando no hay recurso.
    assert set_job_status_mock.await_args_list[0].args[1] == "processing"
    assert len(set_job_status_mock.await_args_list) == 1


@pytest.mark.asyncio
async def test_generate_settlement_async_with_warnings_sets_with_errors(monkeypatch):
    """CA-05-03: "con periodos impagos o cargos faltantes termina
    with_errors"."""
    from adminprop.modules.settlements.repository import UnpaidRentPeriodRow

    @asynccontextmanager
    async def fake_tenant_scoped_session(organization_id):
        yield AsyncMock()

    monkeypatch.setattr(documents_worker, "tenant_scoped_session", fake_tenant_scoped_session)

    fake_repo = AsyncMock()
    fake_repo.get_by_id.return_value = _FakeSettlement(
        landlord_id=uuid.uuid4(), period="2026-06-01", commission_pct_used=Decimal("10.00")
    )
    fake_repo.gather_generation_data.return_value = GatheredSettlementData(
        payments=[], charge_entries=[], repairs=[]
    )
    fake_repo.list_unpaid_rent_periods.return_value = [
        UnpaidRentPeriodRow(rent_period_id=uuid.uuid4(), property_id=uuid.uuid4(), status="pending")
    ]
    fake_repo.list_missing_charge_entries.return_value = []
    fake_repo.apply_calculation = AsyncMock()
    monkeypatch.setattr(documents_worker, "SettlementRepository", lambda session: fake_repo)

    set_job_status_mock = AsyncMock()
    monkeypatch.setattr(documents_worker, "set_job_status", set_job_status_mock)

    await documents_worker._generate_settlement_async(uuid.uuid4(), uuid.uuid4(), "req-3")

    final_call = set_job_status_mock.await_args_list[-1]
    assert final_call.args[1] == "with_errors"
    assert len(final_call.kwargs["warnings"]) == 1


@pytest.mark.asyncio
async def test_generate_settlement_async_non_retryable_error_marks_failed_and_deletes(
    monkeypatch,
):
    """RF-01: "failed: no se genero (error real)" -- decision documentada:
    el placeholder se borra (`delete_placeholder`) para no dejar
    bloqueado el (landlord_id, period)."""

    @asynccontextmanager
    async def fake_tenant_scoped_session(organization_id):
        yield AsyncMock()

    monkeypatch.setattr(documents_worker, "tenant_scoped_session", fake_tenant_scoped_session)

    fake_repo = AsyncMock()
    fake_repo.get_by_id.return_value = _FakeSettlement(
        landlord_id=uuid.uuid4(), period="2026-06-01", commission_pct_used=Decimal("10.00")
    )
    fake_repo.gather_generation_data.side_effect = NonRetryableError("dato invalido")
    fake_repo.delete_placeholder = AsyncMock()
    monkeypatch.setattr(documents_worker, "SettlementRepository", lambda session: fake_repo)

    set_job_status_mock = AsyncMock()
    monkeypatch.setattr(documents_worker, "set_job_status", set_job_status_mock)

    await documents_worker._generate_settlement_async(uuid.uuid4(), uuid.uuid4(), "req-4")

    final_call = set_job_status_mock.await_args_list[-1]
    assert final_call.args[1] == "failed"
    fake_repo.delete_placeholder.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_settlement_async_retryable_error_reraises_and_sets_pending(
    monkeypatch,
):
    """RetryableError debe re-lanzarse para que `DocumentsTask.
    autoretry_for` reintente (docs/skills/async-worker.md)."""

    @asynccontextmanager
    async def fake_tenant_scoped_session(organization_id):
        yield AsyncMock()

    monkeypatch.setattr(documents_worker, "tenant_scoped_session", fake_tenant_scoped_session)

    fake_repo = AsyncMock()
    fake_repo.get_by_id.side_effect = RetryableError("timeout")
    monkeypatch.setattr(documents_worker, "SettlementRepository", lambda session: fake_repo)

    set_job_status_mock = AsyncMock()
    monkeypatch.setattr(documents_worker, "set_job_status", set_job_status_mock)

    with pytest.raises(RetryableError):
        await documents_worker._generate_settlement_async(uuid.uuid4(), uuid.uuid4(), "req-5")

    assert set_job_status_mock.await_args_list[-1].args[1] == "pending"


def test_generate_settlement_task_runs_without_raising(monkeypatch):
    """La tarea Celery delega en el helper async via `asyncio.run`."""
    called = {}

    async def fake_async(settlement_id, organization_id, request_id):
        called["args"] = (settlement_id, organization_id, request_id)

    monkeypatch.setattr(documents_worker, "_generate_settlement_async", fake_async)

    settlement_id = str(uuid.uuid4())
    organization_id = str(uuid.uuid4())
    documents_worker.generate_settlement.apply(args=[settlement_id, organization_id, "req-6"]).get()

    assert called["args"] == (
        uuid.UUID(settlement_id),
        uuid.UUID(organization_id),
        "req-6",
    )


def test_documents_task_retry_policy_matches_async_worker_skill():
    task = documents_worker.DocumentsTask
    assert task.autoretry_for == (RetryableError,)
    assert task.retry_backoff is True
    assert task.retry_backoff_max == 600
    assert task.retry_jitter is True
    assert task.max_retries == 3
