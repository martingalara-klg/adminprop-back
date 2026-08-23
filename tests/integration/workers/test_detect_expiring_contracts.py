"""Issue #19 -- `detect_expiring_contracts`: cuerpo async real contra Postgres.

SDD: spec_module_03_contratos.md §RF-03 (active -> expired automatico) +
     §RF-05 (aviso de vencimiento) + core/sdd_04_nonfunctional.md §1.3
     (Beat itera TODAS las organizaciones `active`).
Implements: CA-03-07 (RN-C05, RN-07, RN-D01).

Mismo criterio que tests/integration/workers/test_detect_due_adjustments.py:
se invoca `_detect_expiring_contracts_async` directamente (no el wrapper
Celery sincronico, que hace `asyncio.run()` y no puede llamarse desde un
test ya corriendo dentro del loop de pytest-asyncio).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_session_factory
from adminprop.workers import notification_worker
from adminprop.workers.notification_worker import _detect_expiring_contracts_async

pytestmark = pytest.mark.asyncio

_TODAY = datetime.now(UTC).date()


async def _seed_organization(
    *, status: str = "active", contract_expiry_notice_days: int | None = None
) -> uuid.UUID:
    """Siembra una organizacion `status` con un owner activo -- mismo
    patron que `test_detect_due_adjustments.py._seed_organization`,
    necesario para que `notifications_service.emit()` (RN-01: solo
    miembros activos de owner/admin) encuentre al menos un destinatario
    real de `contract_expiring`. `contract_expiry_notice_days=None` deja
    `settings` en `{}` (el repository cae al default de 60, CA-03-07)."""
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    role_id = uuid.uuid4()
    settings = (
        {}
        if contract_expiry_notice_days is None
        else {"contract_expiry_notice_days": contract_expiry_notice_days}
    )
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        await session.execute(
            sa.text(
                "INSERT INTO organizations (id, slug, name, status, settings) "
                "VALUES (:id, :slug, :name, :status, :settings)"
            ).bindparams(sa.bindparam("settings", type_=sa.JSON)),
            {
                "id": str(org_id),
                "slug": f"org-{org_id.hex[:8]}",
                "name": f"Org {org_id.hex[:8]}",
                "status": status,
                # `sa.bindparam(type_=sa.JSON)` ya serializa el valor Python
                # a JSON -- pasar `json.dumps(settings)` aca lo serializaria
                # DOS veces (el dict quedaria guardado como un string JSON
                # escapado, y `settings ->> 'key'` sobre un escalar JSON
                # siempre devuelve NULL). Se pasa el dict tal cual.
                "settings": settings,
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
    status: str = "active",
    end_date: date,
    property_status: str = "rented",
) -> tuple[uuid.UUID, uuid.UUID]:
    """Devuelve `(contract_id, property_id)`."""
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
                "'departamento', :property_status)"
            ),
            {
                "id": str(property_id),
                "org_id": str(organization_id),
                "landlord_id": str(landlord_id),
                "property_status": property_status,
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
                "current_amount, start_date, end_date, daily_late_fee_pct, status) "
                "VALUES (:id, :org_id, :property_id, :renter_id, 'ARS', '100000.00', "
                "'100000.00', '2020-01-01', :end_date, '0.1', :status)"
            ),
            {
                "id": str(contract_id),
                "org_id": str(organization_id),
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "end_date": end_date,
                "status": status,
            },
        )
    return contract_id, property_id


async def _contract_row(organization_id: uuid.UUID, contract_id: uuid.UUID) -> dict:
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        result = await session.execute(
            sa.text(
                "SELECT status, expiring_notified_at FROM contracts "
                "WHERE organization_id = :org_id AND id = :contract_id"
            ),
            {"org_id": str(organization_id), "contract_id": str(contract_id)},
        )
        return dict(result.mappings().one())


async def _property_status(organization_id: uuid.UUID, property_id: uuid.UUID) -> str:
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        result = await session.execute(
            sa.text("SELECT status FROM properties WHERE organization_id = :org_id AND id = :id"),
            {"org_id": str(organization_id), "id": str(property_id)},
        )
        return result.scalar_one()


def _calls_for_org(calls: list[tuple], organization_id: uuid.UUID) -> list[tuple]:
    """Filtra las llamadas a `enqueue_pending_emails` de UNA organizacion.

    El job real itera TODAS las organizaciones `active` de la base (mismo
    diseno que `detect_due_adjustments`) -- en una corrida de suite
    completa (sin truncate entre archivos de test) otras organizaciones
    sembradas por tests de otros modulos tambien pueden calificar para el
    aviso de vencimiento. Filtrar por `organization_id` mantiene la
    asercion enfocada en el contrato de ESTE test, sin depender del orden
    ni del aislamiento total de la suite."""
    return [call for call in calls if call[1] == organization_id]


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


class TestCA0307ExpiringNotice:
    """CA-03-07: un contrato que vence dentro del umbral configurado genera
    la notificacion de vencimiento una sola vez, y aparece en el filtro
    `expiring_in_days` (el filtro en si ya esta cubierto por
    tests/integration/contracts/test_contracts_crud.py::TestContractListFilters)."""

    async def test_ca_03_07_notifies_once_when_within_default_threshold(
        self, _mock_enqueue_pending_emails
    ):
        org_id = await _seed_organization()
        contract_id, _ = await _seed_contract(
            organization_id=org_id, end_date=_TODAY + timedelta(days=30)
        )

        await _detect_expiring_contracts_async(request_id="req-expiring-1")

        row = await _contract_row(org_id, contract_id)
        assert row["status"] == "active"
        assert row["expiring_notified_at"] is not None
        assert await _notification_count(org_id, "contract_expiring") == 1
        assert len(_calls_for_org(_mock_enqueue_pending_emails, org_id)) == 1

    async def test_second_run_same_day_does_not_duplicate_notification(
        self, _mock_enqueue_pending_emails
    ):
        """Idempotencia (CA-03-07): re-correr el job no duplica el aviso ya
        enviado -- `expiring_notified_at` ya no es NULL."""
        org_id = await _seed_organization()
        await _seed_contract(organization_id=org_id, end_date=_TODAY + timedelta(days=10))

        await _detect_expiring_contracts_async(request_id="req-expiring-2a")
        await _detect_expiring_contracts_async(request_id="req-expiring-2b")

        assert await _notification_count(org_id, "contract_expiring") == 1

    async def test_contract_outside_threshold_is_not_notified(self, _mock_enqueue_pending_emails):
        org_id = await _seed_organization()
        contract_id, _ = await _seed_contract(
            organization_id=org_id, end_date=_TODAY + timedelta(days=90)
        )

        await _detect_expiring_contracts_async(request_id="req-expiring-3")

        row = await _contract_row(org_id, contract_id)
        assert row["expiring_notified_at"] is None
        assert await _notification_count(org_id, "contract_expiring") == 0

    async def test_draft_contract_is_never_considered(self, _mock_enqueue_pending_emails):
        org_id = await _seed_organization()
        contract_id, _ = await _seed_contract(
            organization_id=org_id, status="draft", end_date=_TODAY + timedelta(days=5)
        )

        await _detect_expiring_contracts_async(request_id="req-expiring-4")

        row = await _contract_row(org_id, contract_id)
        assert row["expiring_notified_at"] is None
        assert await _notification_count(org_id, "contract_expiring") == 0

    async def test_organization_specific_threshold_is_respected(self, _mock_enqueue_pending_emails):
        """RF-05: "default 60, configurable por org" -- una organizacion
        con umbral de 10 dias no notifica un contrato que vence en 20."""
        org_id = await _seed_organization(contract_expiry_notice_days=10)
        contract_id, _ = await _seed_contract(
            organization_id=org_id, end_date=_TODAY + timedelta(days=20)
        )

        await _detect_expiring_contracts_async(request_id="req-expiring-5")

        row = await _contract_row(org_id, contract_id)
        assert row["expiring_notified_at"] is None
        assert await _notification_count(org_id, "contract_expiring") == 0

    async def test_organization_not_active_is_never_iterated(self, _mock_enqueue_pending_emails):
        org_id = await _seed_organization(status="pending_owner")
        contract_id, _ = await _seed_contract(
            organization_id=org_id, end_date=_TODAY + timedelta(days=5)
        )

        await _detect_expiring_contracts_async(request_id="req-expiring-6")

        row = await _contract_row(org_id, contract_id)
        assert row["expiring_notified_at"] is None

    async def test_two_organizations_are_isolated_from_each_other(
        self, _mock_enqueue_pending_emails
    ):
        org_a = await _seed_organization()
        org_b = await _seed_organization()
        contract_a, _ = await _seed_contract(
            organization_id=org_a, end_date=_TODAY + timedelta(days=5)
        )
        contract_b, _ = await _seed_contract(
            organization_id=org_b, end_date=_TODAY + timedelta(days=200)
        )

        await _detect_expiring_contracts_async(request_id="req-expiring-7")

        row_a = await _contract_row(org_a, contract_a)
        row_b = await _contract_row(org_b, contract_b)
        assert row_a["expiring_notified_at"] is not None
        assert row_b["expiring_notified_at"] is None


class TestRNC05AutomaticExpiry:
    """RF-03: `active -> expired` automatico al pasar `end_date` (RN-C05/
    RN-07). Sin CA-XX dedicado en el SDD (RF-03 lo describe, CA-03-08
    cubre la transicion MANUAL via `terminate`, issue #17) -- se nombra
    con el ID de la regla de negocio, siguiendo la convencion de
    docs/skills/testing.md ("RN-P02 -> test_rn_p02_...")."""

    async def test_rn_c05_contract_past_end_date_transitions_to_expired_and_property_available(
        self, _mock_enqueue_pending_emails
    ):
        org_id = await _seed_organization()
        contract_id, property_id = await _seed_contract(
            organization_id=org_id,
            end_date=_TODAY - timedelta(days=1),
            property_status="rented",
        )

        await _detect_expiring_contracts_async(request_id="req-expired-1")

        row = await _contract_row(org_id, contract_id)
        assert row["status"] == "expired"
        assert await _property_status(org_id, property_id) == "available"

    async def test_rn_c05_expired_contract_is_not_also_notified_as_expiring(
        self, _mock_enqueue_pending_emails
    ):
        """Un contrato que ya vencio (y pasa a `expired` en el paso 1) no
        debe generar ademas el aviso `contract_expiring` -- ya no esta
        `active` cuando el paso 2 lo evalua."""
        org_id = await _seed_organization()
        contract_id, _ = await _seed_contract(
            organization_id=org_id, end_date=_TODAY - timedelta(days=1)
        )

        await _detect_expiring_contracts_async(request_id="req-expired-2")

        row = await _contract_row(org_id, contract_id)
        assert row["expiring_notified_at"] is None
        assert await _notification_count(org_id, "contract_expiring") == 0

    async def test_terminated_contract_is_never_reconsidered_for_expiry(
        self, _mock_enqueue_pending_emails
    ):
        org_id = await _seed_organization()
        contract_id, property_id = await _seed_contract(
            organization_id=org_id,
            status="terminated",
            end_date=_TODAY - timedelta(days=1),
            property_status="available",
        )

        await _detect_expiring_contracts_async(request_id="req-expired-3")

        row = await _contract_row(org_id, contract_id)
        assert row["status"] == "terminated"
        assert await _property_status(org_id, property_id) == "available"
