"""Issue #18 -- `detect_due_adjustments`: cuerpo async real contra Postgres.

SDD: spec_module_03_contratos.md §RF-04 paso 1 + core/sdd_04_nonfunctional.md
§1.3 (Beat itera TODAS las organizaciones `active`).
Implements: CA-03-04 (RN-C03).

Mismo criterio que tests/integration/workers/test_notification_worker_outbox.py:
se invoca `_detect_due_adjustments_async` directamente (no el wrapper
Celery sincronico, que hace `asyncio.run()` y no puede llamarse desde un
test ya corriendo dentro del loop de pytest-asyncio).
"""

from __future__ import annotations

import json
import uuid
from datetime import date

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_session_factory
from adminprop.workers import notification_worker
from adminprop.workers.notification_worker import _detect_due_adjustments_async

pytestmark = pytest.mark.asyncio

# Todos los contratos de este test siembran una vigencia larga y fija --
# no ejercitan solapamiento ni vencimiento, solo la deteccion de ajustes.
_FAR_END_DATE = date(2040, 1, 1)


async def _seed_organization(*, status: str = "active") -> uuid.UUID:
    """Siembra una organizacion `status` con un owner activo -- mismo
    patron que `test_notification_worker_outbox.py._seed_org_with_owner_and_notification`,
    necesario para que `notifications_service.emit()` (RN-01: solo
    miembros activos de owner/admin) encuentre al menos un destinatario
    real de `adjustment_pending`."""
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    role_id = uuid.uuid4()
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        await session.execute(
            sa.text(
                "INSERT INTO organizations (id, slug, name, status) "
                "VALUES (:id, :slug, :name, :status)"
            ),
            {
                "id": str(org_id),
                "slug": f"org-{org_id.hex[:8]}",
                "name": f"Org {org_id.hex[:8]}",
                "status": status,
            },
        )
        await session.execute(
            sa.text(
                "INSERT INTO users (id, email, password_hash, full_name, is_super_admin) "
                "VALUES (:id, :email, 'x', 'Owner', FALSE)"
            ),
            {"id": str(user_id), "email": f"owner-{user_id.hex[:8]}@example.com"},
        )
        await session.execute(
            sa.text(
                "INSERT INTO roles (id, organization_id, name, permissions) "
                "VALUES (:id, :org_id, 'owner', :permissions)"
            ).bindparams(sa.bindparam("permissions", type_=sa.JSON)),
            {"id": str(role_id), "org_id": str(org_id), "permissions": json.dumps([])},
        )
        await session.execute(
            sa.text(
                "INSERT INTO organization_members (organization_id, user_id, role_id, status) "
                "VALUES (:org_id, :user_id, :role_id, 'active')"
            ),
            {"org_id": str(org_id), "user_id": str(user_id), "role_id": str(role_id)},
        )
    return org_id


async def _seed_contract(
    *,
    organization_id: uuid.UUID,
    currency: str = "ARS",
    status: str = "active",
    start_date: str = "2026-01-01",
    adjustment_frequency_months: int | None = 3,
    current_amount: str = "100000.00",
) -> uuid.UUID:
    landlord_id = uuid.uuid4()
    property_id = uuid.uuid4()
    renter_id = uuid.uuid4()
    contract_id = uuid.uuid4()
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        await session.execute(
            sa.text(
                "INSERT INTO landlords (id, organization_id, name, commission_pct) "
                "VALUES (:id, :org_id, 'Propietario', '10.00')"
            ),
            {"id": str(landlord_id), "org_id": str(organization_id)},
        )
        await session.execute(
            sa.text(
                "INSERT INTO properties "
                "(id, organization_id, landlord_id, address, property_type, status) "
                "VALUES (:id, :org_id, :landlord_id, 'Direccion de prueba', "
                "'departamento', 'rented')"
            ),
            {
                "id": str(property_id),
                "org_id": str(organization_id),
                "landlord_id": str(landlord_id),
            },
        )
        await session.execute(
            sa.text(
                "INSERT INTO renters (id, organization_id, name) VALUES (:id, :org_id, 'Inquilino')"
            ),
            {"id": str(renter_id), "org_id": str(organization_id)},
        )
        await session.execute(
            sa.text(
                "INSERT INTO contracts "
                "(id, organization_id, property_id, renter_id, currency, initial_amount, "
                "current_amount, start_date, end_date, daily_late_fee_pct, "
                "adjustment_frequency_months, adjustment_index, status) "
                "VALUES (:id, :org_id, :property_id, :renter_id, :currency, :amount, :amount, "
                ":start_date, :end_date, '0.1', :freq, :idx, :status)"
            ),
            {
                "id": str(contract_id),
                "org_id": str(organization_id),
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "currency": currency,
                "amount": current_amount,
                "start_date": date.fromisoformat(start_date),
                "end_date": _FAR_END_DATE,
                "freq": adjustment_frequency_months,
                "idx": "icl" if adjustment_frequency_months is not None else None,
                "status": status,
            },
        )
    return contract_id


async def _pending_adjustments(organization_id: uuid.UUID, contract_id: uuid.UUID) -> list[dict]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        result = await session.execute(
            sa.text(
                "SELECT id, due_period, status, previous_amount FROM contract_adjustments "
                "WHERE organization_id = :org_id AND contract_id = :contract_id"
            ),
            {"org_id": str(organization_id), "contract_id": str(contract_id)},
        )
        return [dict(row._mapping) for row in result]


async def _notification_count(organization_id: uuid.UUID, event_type: str) -> int:
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        result = await session.execute(
            sa.text(
                "SELECT count(*) FROM notifications "
                "WHERE organization_id = :org_id AND event_type = :event_type"
            ),
            {"org_id": str(organization_id), "event_type": event_type},
        )
        return result.scalar_one()


@pytest.fixture(autouse=True)
def _mock_enqueue_pending_emails(monkeypatch):
    """El outbox de email (`enqueue_pending_emails`) es responsabilidad
    separada, ya cubierta por tests/integration/workers/test_notification_worker_outbox.py
    -- se mockea aca para no depender del broker Celery/Redis en este test."""
    calls: list[tuple] = []

    def _fake_enqueue(notification_ids, *, organization_id, request_id):
        calls.append((notification_ids, organization_id, request_id))

    monkeypatch.setattr(notification_worker, "enqueue_pending_emails", _fake_enqueue)
    return calls


class TestCA0304DetectDueAdjustments:
    """CA-03-04: al llegar el mes de ajuste, el sistema crea el ajuste
    `pending` y notifica."""

    async def test_ca_03_04_creates_pending_adjustment_when_due_and_notifies(
        self, _mock_enqueue_pending_emails
    ):
        org_id = await _seed_organization()
        contract_id = await _seed_contract(
            organization_id=org_id, start_date="2026-01-01", adjustment_frequency_months=3
        )

        await _detect_due_adjustments_async(request_id="req-detect-1")

        rows = await _pending_adjustments(org_id, contract_id)
        assert len(rows) == 1
        assert rows[0]["status"] == "pending"
        assert rows[0]["due_period"] == date(2026, 4, 1)
        assert str(rows[0]["previous_amount"]) == "100000.00"
        assert await _notification_count(org_id, "adjustment_pending") == 1
        assert len(_mock_enqueue_pending_emails) == 1

    async def test_contract_not_yet_due_is_skipped(self, _mock_enqueue_pending_emails):
        org_id = await _seed_organization()
        contract_id = await _seed_contract(
            organization_id=org_id, start_date="2026-08-01", adjustment_frequency_months=6
        )

        await _detect_due_adjustments_async(request_id="req-detect-2")

        rows = await _pending_adjustments(org_id, contract_id)
        assert rows == []

    async def test_usd_contract_is_never_considered(self, _mock_enqueue_pending_emails):
        org_id = await _seed_organization()
        contract_id = await _seed_contract(
            organization_id=org_id,
            currency="USD",
            start_date="2026-01-01",
            adjustment_frequency_months=None,
        )

        await _detect_due_adjustments_async(request_id="req-detect-3")

        rows = await _pending_adjustments(org_id, contract_id)
        assert rows == []

    async def test_draft_contract_is_never_considered(self, _mock_enqueue_pending_emails):
        org_id = await _seed_organization()
        contract_id = await _seed_contract(
            organization_id=org_id,
            status="draft",
            start_date="2026-01-01",
            adjustment_frequency_months=1,
        )

        await _detect_due_adjustments_async(request_id="req-detect-4")

        rows = await _pending_adjustments(org_id, contract_id)
        assert rows == []

    async def test_organization_not_active_is_never_iterated(self, _mock_enqueue_pending_emails):
        org_id = await _seed_organization(status="pending_owner")
        contract_id = await _seed_contract(
            organization_id=org_id, start_date="2026-01-01", adjustment_frequency_months=1
        )

        await _detect_due_adjustments_async(request_id="req-detect-5")

        rows = await _pending_adjustments(org_id, contract_id)
        assert rows == []

    async def test_idempotent_second_run_same_day_does_not_duplicate_pending(
        self, _mock_enqueue_pending_emails
    ):
        """Idempotencia (RN-C03/CA-16-03): re-correr el job el mismo dia no
        duplica el ajuste `pending` ya creado."""
        org_id = await _seed_organization()
        contract_id = await _seed_contract(
            organization_id=org_id, start_date="2026-01-01", adjustment_frequency_months=3
        )

        await _detect_due_adjustments_async(request_id="req-detect-6a")
        await _detect_due_adjustments_async(request_id="req-detect-6b")

        rows = await _pending_adjustments(org_id, contract_id)
        assert len(rows) == 1

    async def test_next_due_period_counted_from_last_applied_adjustment(
        self, _mock_enqueue_pending_emails
    ):
        """RN-C03: el ancla es el `due_period` del ultimo ajuste `applied`,
        no `start_date`, cuando ya existe uno."""
        org_id = await _seed_organization()
        contract_id = await _seed_contract(
            organization_id=org_id, start_date="2026-01-01", adjustment_frequency_months=3
        )
        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
            await session.execute(
                sa.text(
                    "INSERT INTO contract_adjustments "
                    "(id, organization_id, contract_id, due_period, status, "
                    "previous_amount, pct_applied, new_amount, applied_at) "
                    "VALUES (:id, :org_id, :contract_id, '2026-04-01', 'applied', "
                    "'100000.00', '10.00', '110000.00', now())"
                ),
                {"id": str(uuid.uuid4()), "org_id": str(org_id), "contract_id": str(contract_id)},
            )

        await _detect_due_adjustments_async(request_id="req-detect-7")

        rows = await _pending_adjustments(org_id, contract_id)
        pending_rows = [r for r in rows if r["status"] == "pending"]
        assert len(pending_rows) == 1
        # Anclado en 2026-04-01 (ultimo applied) + 3 meses = 2026-07-01,
        # ya vencido para "hoy" (fecha real de ejecucion del test).
        assert pending_rows[0]["due_period"] == date(2026, 7, 1)

    async def test_two_organizations_are_isolated_from_each_other(
        self, _mock_enqueue_pending_emails
    ):
        org_a = await _seed_organization()
        org_b = await _seed_organization()
        contract_a = await _seed_contract(
            organization_id=org_a, start_date="2026-01-01", adjustment_frequency_months=1
        )
        contract_b = await _seed_contract(
            organization_id=org_b, start_date="2030-01-01", adjustment_frequency_months=1
        )

        await _detect_due_adjustments_async(request_id="req-detect-8")

        rows_a = await _pending_adjustments(org_a, contract_a)
        rows_b = await _pending_adjustments(org_b, contract_b)
        assert len(rows_a) == 1
        assert rows_b == []
