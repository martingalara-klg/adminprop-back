"""Fixtures compartidas de `tests/integration/workers`.

Mismo motivo que `tests/integration/db/conftest.py` (issue #3):
`adminprop.db.session.get_engine`/`get_session_factory` estan cacheadas
con `lru_cache` a nivel de proceso; pytest-asyncio crea un event loop
nuevo por test, asi que reusar el engine cacheado entre tests ata sus
conexiones asyncpg a un loop ya cerrado.

`seed` (issue #29): Seeder minimo para `test_documents_worker.py`
(organizacion/usuario/landlord/renter/propiedad/contrato/rent_period/
payment/recurring_charge/charge_entry/work_order) -- mismo criterio de
duplicacion deliberada que `tests/integration/settlements/conftest.py`
(sin las partes HTTP/JWT, que este directorio no necesita: los tests de
worker invocan `_generate_settlement_async` directamente, no via
`client`). Redis se limpia tambien: `job_status.py` (issue #29) ahora lo
usa.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import date

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_engine, get_session_factory
from adminprop.shared.auth.passwords import hash_password
from adminprop.shared.cache.redis import get_redis_client


@pytest.fixture(autouse=True)
async def _fresh_engine_per_test() -> AsyncGenerator[None]:
    get_session_factory.cache_clear()
    get_engine.cache_clear()
    get_redis_client.cache_clear()
    yield
    engine = get_engine()
    await engine.dispose()
    get_session_factory.cache_clear()
    get_engine.cache_clear()
    redis = get_redis_client()
    await redis.flushdb()
    await redis.aclose()
    get_redis_client.cache_clear()


def _unique_email() -> str:
    return f"user-{uuid.uuid4().hex[:12]}@example.com"


@pytest.fixture()
def seed():
    class Seeder:
        async def create_user(self) -> dict:
            user_id = uuid.uuid4()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO users (id, email, password_hash, full_name, is_super_admin) "
                        "VALUES (:id, :email, :password_hash, :full_name, false)"
                    ),
                    {
                        "id": str(user_id),
                        "email": _unique_email(),
                        "password_hash": hash_password("Password1234"),
                        "full_name": "Test User",
                    },
                )
            return {"id": user_id}

        async def create_organization(self) -> uuid.UUID:
            org_id = uuid.uuid4()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO organizations (id, slug, name, status) "
                        "VALUES (:id, :slug, :name, 'active')"
                    ),
                    {"id": str(org_id), "slug": f"org-{org_id.hex[:8]}", "name": "Org worker test"},
                )
            return org_id

        async def create_landlord_row(
            self, *, organization_id: uuid.UUID, commission_pct: str = "10.00"
        ) -> uuid.UUID:
            landlord_id = uuid.uuid4()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO landlords (id, organization_id, name, commission_pct) "
                        "VALUES (:id, :org_id, 'Propietario de prueba', :commission_pct)"
                    ),
                    {
                        "id": str(landlord_id),
                        "org_id": str(organization_id),
                        "commission_pct": commission_pct,
                    },
                )
            return landlord_id

        async def create_renter_row(self, *, organization_id: uuid.UUID) -> uuid.UUID:
            renter_id = uuid.uuid4()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO renters (id, organization_id, name) "
                        "VALUES (:id, :org_id, 'Inquilino de prueba')"
                    ),
                    {"id": str(renter_id), "org_id": str(organization_id)},
                )
            return renter_id

        async def create_property_row(
            self, *, organization_id: uuid.UUID, landlord_id: uuid.UUID, address: str = "Av. Test"
        ) -> uuid.UUID:
            property_id = uuid.uuid4()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO properties "
                        "(id, organization_id, landlord_id, address, property_type, status) "
                        "VALUES (:id, :org_id, :landlord_id, :address, 'departamento', 'rented')"
                    ),
                    {
                        "id": str(property_id),
                        "org_id": str(organization_id),
                        "landlord_id": str(landlord_id),
                        "address": address,
                    },
                )
            return property_id

        async def create_contract_row(
            self, *, organization_id: uuid.UUID, property_id: uuid.UUID, renter_id: uuid.UUID
        ) -> uuid.UUID:
            contract_id = uuid.uuid4()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO contracts "
                        "(id, organization_id, property_id, renter_id, currency, "
                        "initial_amount, current_amount, start_date, end_date, "
                        "daily_late_fee_pct, status) "
                        "VALUES (:id, :org_id, :property_id, :renter_id, 'ARS', "
                        "100000.00, 100000.00, '2026-01-01', '2027-01-01', 0.1, 'active')"
                    ),
                    {
                        "id": str(contract_id),
                        "org_id": str(organization_id),
                        "property_id": str(property_id),
                        "renter_id": str(renter_id),
                    },
                )
            return contract_id

        async def create_rent_period_row(
            self,
            *,
            organization_id: uuid.UUID,
            contract_id: uuid.UUID,
            period: str = "2026-06-01",
            currency: str = "ARS",
            status: str = "pending",
            amount_due: str = "100000.00",
            paid_total: str = "0.00",
        ) -> uuid.UUID:
            rent_period_id = uuid.uuid4()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO rent_periods "
                        "(id, organization_id, contract_id, period, amount_due, currency, "
                        "status, paid_total) "
                        "VALUES (:id, :org_id, :contract_id, :period, :amount_due, :currency, "
                        ":status, :paid_total)"
                    ),
                    {
                        "id": str(rent_period_id),
                        "org_id": str(organization_id),
                        "contract_id": str(contract_id),
                        "period": date.fromisoformat(period),
                        "amount_due": amount_due,
                        "currency": currency,
                        "status": status,
                        "paid_total": paid_total,
                    },
                )
            return rent_period_id

        async def create_payment_row(
            self,
            *,
            organization_id: uuid.UUID,
            rent_period_id: uuid.UUID,
            created_by: uuid.UUID,
            amount: str = "1000.00",
            payment_currency: str = "ARS",
            destination: str = "agency_account",
            charged_interest: str = "0.00",
        ) -> uuid.UUID:
            payment_id = uuid.uuid4()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO payments "
                        "(id, organization_id, rent_period_id, payment_date, method, "
                        "payment_currency, amount, destination, charged_interest, created_by) "
                        "VALUES (:id, :org_id, :rent_period_id, '2026-06-05', 'cash', "
                        ":payment_currency, :amount, :destination, :charged_interest, :created_by)"
                    ),
                    {
                        "id": str(payment_id),
                        "org_id": str(organization_id),
                        "rent_period_id": str(rent_period_id),
                        "payment_currency": payment_currency,
                        "amount": amount,
                        "destination": destination,
                        "charged_interest": charged_interest,
                        "created_by": str(created_by),
                    },
                )
            return payment_id

        async def create_recurring_charge_row(
            self, *, organization_id: uuid.UUID, property_id: uuid.UUID
        ) -> uuid.UUID:
            recurring_charge_id = uuid.uuid4()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO recurring_charges "
                        "(id, organization_id, property_id, charge_type, label) "
                        "VALUES (:id, :org_id, :property_id, 'rentas', 'Rentas')"
                    ),
                    {
                        "id": str(recurring_charge_id),
                        "org_id": str(organization_id),
                        "property_id": str(property_id),
                    },
                )
            return recurring_charge_id

        async def create_charge_entry_row(
            self,
            *,
            organization_id: uuid.UUID,
            recurring_charge_id: uuid.UUID,
            created_by: uuid.UUID,
            amount: str = "5000.00",
            period: str = "2026-06-01",
        ) -> uuid.UUID:
            charge_entry_id = uuid.uuid4()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO charge_entries "
                        "(id, organization_id, recurring_charge_id, period, amount, created_by) "
                        "VALUES (:id, :org_id, :recurring_charge_id, :period, :amount, :created_by)"
                    ),
                    {
                        "id": str(charge_entry_id),
                        "org_id": str(organization_id),
                        "recurring_charge_id": str(recurring_charge_id),
                        "period": date.fromisoformat(period),
                        "amount": amount,
                        "created_by": str(created_by),
                    },
                )
            return charge_entry_id

        async def create_work_order_row(
            self,
            *,
            organization_id: uuid.UUID,
            property_id: uuid.UUID,
            created_by: uuid.UUID,
            payer: str = "agency",
            status: str = "closed",
            final_cost: str = "2000.00",
        ) -> uuid.UUID:
            work_order_id = uuid.uuid4()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO work_orders "
                        "(id, organization_id, property_id, title, payer, status, "
                        "final_cost, created_by) "
                        "VALUES (:id, :org_id, :property_id, 'Reparacion de prueba', :payer, "
                        ":status, :final_cost, :created_by)"
                    ),
                    {
                        "id": str(work_order_id),
                        "org_id": str(organization_id),
                        "property_id": str(property_id),
                        "payer": payer,
                        "status": status,
                        "final_cost": final_cost,
                        "created_by": str(created_by),
                    },
                )
            return work_order_id

        async def get_settlement_row(self, settlement_id: uuid.UUID) -> dict:
            session_factory = get_session_factory()
            async with session_factory() as session:
                result = await session.execute(
                    sa.text("SELECT * FROM settlements WHERE id = :id"),
                    {"id": str(settlement_id)},
                )
                return dict(result.mappings().one())

        async def get_work_order_row(self, work_order_id: uuid.UUID) -> dict:
            session_factory = get_session_factory()
            async with session_factory() as session:
                result = await session.execute(
                    sa.text(
                        "SELECT status, settled_in_settlement_id FROM work_orders WHERE id = :id"
                    ),
                    {"id": str(work_order_id)},
                )
                return dict(result.mappings().one())

        async def get_line_items(self, settlement_id: uuid.UUID) -> list[dict]:
            """Issue #30 -- CA-05-05/CA-05-06 (regeneracion): mismo helper
            que `tests/integration/settlements/conftest.py`."""
            session_factory = get_session_factory()
            async with session_factory() as session:
                result = await session.execute(
                    sa.text(
                        "SELECT * FROM settlement_line_items WHERE settlement_id = :id "
                        "ORDER BY created_at"
                    ),
                    {"id": str(settlement_id)},
                )
                return [dict(row._mapping) for row in result]

        async def get_attachments(self, entity_id: uuid.UUID) -> list[dict]:
            session_factory = get_session_factory()
            async with session_factory() as session:
                result = await session.execute(
                    sa.text("SELECT * FROM attachments WHERE entity_id = :id ORDER BY created_at"),
                    {"id": str(entity_id)},
                )
                return [dict(row._mapping) for row in result]

        async def audit_rows(self, organization_id: uuid.UUID, action: str) -> list[dict]:
            session_factory = get_session_factory()
            async with session_factory() as session:
                result = await session.execute(
                    sa.text(
                        "SELECT entity_id, user_id, before_state, after_state FROM audit_logs "
                        "WHERE organization_id = :org_id AND action = :action ORDER BY created_at"
                    ),
                    {"org_id": str(organization_id), "action": action},
                )
                return [dict(row._mapping) for row in result]

    return Seeder()
