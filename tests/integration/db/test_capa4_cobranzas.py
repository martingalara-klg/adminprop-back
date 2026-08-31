"""Issue #20 — Migracion Capa 4: rent_periods + payments,
schema/RLS/CHECK/UNIQUE/indices.

Requiere Postgres real con `alembic upgrade head` ya corrido -- mismo
patron que `tests/integration/db/test_capa3_contratos.py` (issue #16).

SDD: infrastructure/spec_data_model.md §Capa 4 — Cobranzas
Implements: CA-20-01 (UNIQUE (contract_id, period) probado),
            CA-20-02 (CHECK paid_total <= amount_due probado),
            CA-20-03 (indices del spec verificados con pg_indexes)
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import date

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from adminprop.db.session import get_engine, get_session_factory

pytestmark = pytest.mark.asyncio

_RENT_PERIODS_COLUMNS = {
    "id",
    "organization_id",
    "contract_id",
    "period",
    "amount_due",
    "currency",
    "status",
    "paid_total",
    "created_at",
    "updated_at",
}

_PAYMENTS_COLUMNS = {
    "id",
    "organization_id",
    "rent_period_id",
    "payment_date",
    "method",
    "payment_currency",
    "amount",
    "exchange_rate",
    "destination",
    "suggested_interest",
    "charged_interest",
    "forgiven_interest",
    "days_late",
    "notes",
    "voided_at",
    "voided_by",
    "created_by",
    "origin",
    "created_at",
    "updated_at",
}


class _Rows:
    def __init__(
        self,
        *,
        organization_id: uuid.UUID,
        contract_id: uuid.UUID,
        contract_b_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        self.organization_id = organization_id
        self.contract_id = contract_id
        self.contract_b_id = contract_b_id
        self.user_id = user_id


@pytest.fixture
async def rows() -> AsyncGenerator[_Rows]:
    """Siembra una organizacion con landlord + property + renter + dos
    contratos (para probar UNIQUE por contrato) + un usuario -- suficiente
    para ejercer los constraints de `rent_periods`/`payments` sin depender
    de otro modulo (mismo criterio que
    `tests/integration/db/test_capa3_contratos.py`)."""
    session_factory = get_session_factory()
    org_id = uuid.uuid4()
    landlord_id = uuid.uuid4()
    property_id = uuid.uuid4()
    renter_id = uuid.uuid4()
    contract_id = uuid.uuid4()
    contract_b_id = uuid.uuid4()
    user_id = uuid.uuid4()

    async with session_factory() as session, session.begin():
        # issue #42: bootstrap cruza organizations/landlords (grants
        # restringidos para adminprop_app) -- bypass RLS/grants.
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        await session.execute(
            sa.text(
                "INSERT INTO organizations (id, slug, name) VALUES (:id, :slug, 'Org Cobranzas')"
            ),
            {"id": str(org_id), "slug": f"org-{org_id.hex[:8]}"},
        )
        await session.execute(
            sa.text(
                "INSERT INTO users (id, email, password_hash, full_name) "
                "VALUES (:id, :email, 'hash', 'Operador Cobranzas')"
            ),
            {"id": str(user_id), "email": f"{user_id.hex[:8]}@adminprop.test"},
        )
        await session.execute(
            sa.text(
                "INSERT INTO landlords (id, organization_id, name, commission_pct) "
                "VALUES (:id, :org_id, 'Landlord', 10)"
            ),
            {"id": str(landlord_id), "org_id": str(org_id)},
        )
        await session.execute(
            sa.text(
                "INSERT INTO properties (id, organization_id, landlord_id, address) "
                "VALUES (:id, :org_id, :landlord_id, 'Propiedad Cobranzas')"
            ),
            {"id": str(property_id), "org_id": str(org_id), "landlord_id": str(landlord_id)},
        )
        await session.execute(
            sa.text(
                "INSERT INTO renters (id, organization_id, name) VALUES (:id, :org_id, 'Renter')"
            ),
            {"id": str(renter_id), "org_id": str(org_id)},
        )
        await session.execute(
            sa.text(
                "INSERT INTO contracts "
                "(id, organization_id, property_id, renter_id, currency, initial_amount, "
                "current_amount, start_date, end_date, daily_late_fee_pct, status) "
                "VALUES "
                "(:contract_id, :org_id, :property_id, :renter_id, 'ARS', 100000, 100000, "
                ":start_date, :end_date, 0.5, 'active')"
            ),
            {
                "contract_id": str(contract_id),
                "org_id": str(org_id),
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "start_date": date(2026, 1, 1),
                "end_date": date(2026, 12, 31),
            },
        )
        # Un segundo contrato (draft, sin solapar RN-C01 por estar draft)
        # sobre la misma propiedad, para probar que el UNIQUE de
        # rent_periods es por-contrato y no por-propiedad.
        await session.execute(
            sa.text(
                "INSERT INTO contracts "
                "(id, organization_id, property_id, renter_id, currency, initial_amount, "
                "current_amount, start_date, end_date, daily_late_fee_pct, status) "
                "VALUES "
                "(:contract_id, :org_id, :property_id, :renter_id, 'ARS', 100000, 100000, "
                ":start_date, :end_date, 0.5, 'draft')"
            ),
            {
                "contract_id": str(contract_b_id),
                "org_id": str(org_id),
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "start_date": date(2026, 1, 1),
                "end_date": date(2026, 12, 31),
            },
        )

    yield _Rows(
        organization_id=org_id,
        contract_id=contract_id,
        contract_b_id=contract_b_id,
        user_id=user_id,
    )

    async with session_factory() as session, session.begin():
        # issue #42: teardown cruza el bootstrap de la organizacion -- bypass RLS.
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        await session.execute(
            sa.text(
                "DELETE FROM payments WHERE rent_period_id IN "
                "(SELECT id FROM rent_periods WHERE organization_id = :org_id)"
            ),
            {"org_id": str(org_id)},
        )
        await session.execute(
            sa.text("DELETE FROM rent_periods WHERE organization_id = :org_id"),
            {"org_id": str(org_id)},
        )
        await session.execute(
            sa.text("DELETE FROM contracts WHERE organization_id = :org_id"),
            {"org_id": str(org_id)},
        )
        await session.execute(
            sa.text("DELETE FROM properties WHERE organization_id = :org_id"),
            {"org_id": str(org_id)},
        )
        await session.execute(
            sa.text("DELETE FROM renters WHERE organization_id = :org_id"),
            {"org_id": str(org_id)},
        )
        await session.execute(
            sa.text("DELETE FROM landlords WHERE organization_id = :org_id"),
            {"org_id": str(org_id)},
        )
        await session.execute(
            sa.text("DELETE FROM users WHERE id = :user_id"),
            {"user_id": str(user_id)},
        )
        await session.execute(
            sa.text("DELETE FROM organizations WHERE id = :org_id"),
            {"org_id": str(org_id)},
        )


async def _insert_rent_period(
    rows: _Rows,
    *,
    contract_id: uuid.UUID | None = None,
    period: date = date(2026, 8, 1),
    amount_due: str = "100000.00",
    currency: str = "ARS",
    status: str = "pending",
    paid_total: str = "0",
) -> uuid.UUID:
    rent_period_id = uuid.uuid4()
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        # issue #42: no se testea tenant isolation aca -- bypass RLS.
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        await session.execute(
            sa.text(
                "INSERT INTO rent_periods "
                "(id, organization_id, contract_id, period, amount_due, currency, status, "
                "paid_total) "
                "VALUES (:id, :org_id, :contract_id, :period, :amount_due, :currency, :status, "
                ":paid_total)"
            ),
            {
                "id": str(rent_period_id),
                "org_id": str(rows.organization_id),
                "contract_id": str(contract_id or rows.contract_id),
                "period": period,
                "amount_due": amount_due,
                "currency": currency,
                "status": status,
                "paid_total": paid_total,
            },
        )
    return rent_period_id


async def _insert_payment(
    rows: _Rows,
    *,
    rent_period_id: uuid.UUID,
    payment_date: date = date(2026, 8, 10),
    method: str = "cash",
    payment_currency: str = "ARS",
    amount: str = "50000.00",
    exchange_rate: str | None = None,
    destination: str = "agency_account",
) -> uuid.UUID:
    payment_id = uuid.uuid4()
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        # issue #42: no se testea tenant isolation aca -- bypass RLS.
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        await session.execute(
            sa.text(
                "INSERT INTO payments "
                "(id, organization_id, rent_period_id, payment_date, method, "
                "payment_currency, amount, exchange_rate, destination, created_by) "
                "VALUES (:id, :org_id, :rent_period_id, :payment_date, :method, "
                ":payment_currency, :amount, :exchange_rate, :destination, :created_by)"
            ),
            {
                "id": str(payment_id),
                "org_id": str(rows.organization_id),
                "rent_period_id": str(rent_period_id),
                "payment_date": payment_date,
                "method": method,
                "payment_currency": payment_currency,
                "amount": amount,
                "exchange_rate": exchange_rate,
                "destination": destination,
                "created_by": str(rows.user_id),
            },
        )
    return payment_id


# ─── CA-20-01: schema identico al spec ─────────────────────────────────────


async def test_ca_20_01_rent_periods_columnas_identicas_al_spec():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'rent_periods'"
            )
        )
        columns = {row[0] for row in result}
    assert columns == _RENT_PERIODS_COLUMNS


async def test_ca_20_01_payments_columnas_identicas_al_spec():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'payments'"
            )
        )
        columns = {row[0] for row in result}
    assert columns == _PAYMENTS_COLUMNS


async def test_ca_20_01_rent_periods_status_default_es_pending():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = 'rent_periods' AND column_name = 'status'"
            )
        )
        default = result.scalar_one()
    assert "pending" in default


async def test_ca_20_01_rent_periods_paid_total_default_es_cero():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = 'rent_periods' AND column_name = 'paid_total'"
            )
        )
        default = result.scalar_one()
    assert "0" in default


async def test_ca_20_01_fk_rent_periods_contract_id_referencia_contracts():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT confrelid::regclass::text FROM pg_constraint "
                "WHERE conrelid = 'rent_periods'::regclass AND contype = 'f' "
                "AND conname LIKE '%contract_id%'"
            )
        )
        referenced_table = result.scalar_one()
    assert referenced_table == "contracts"


async def test_ca_20_01_fk_payments_rent_period_id_referencia_rent_periods():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT confrelid::regclass::text FROM pg_constraint "
                "WHERE conrelid = 'payments'::regclass AND contype = 'f' "
                "AND conname LIKE '%rent_period_id%'"
            )
        )
        referenced_table = result.scalar_one()
    assert referenced_table == "rent_periods"


async def test_ca_20_01_fk_payments_created_by_referencia_users():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT confrelid::regclass::text FROM pg_constraint "
                "WHERE conrelid = 'payments'::regclass AND contype = 'f' "
                "AND conname LIKE '%created_by%'"
            )
        )
        referenced_table = result.scalar_one()
    assert referenced_table == "users"


async def test_ca_20_01_fk_payments_voided_by_referencia_users():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT confrelid::regclass::text FROM pg_constraint "
                "WHERE conrelid = 'payments'::regclass AND contype = 'f' "
                "AND conname LIKE '%voided_by%'"
            )
        )
        referenced_table = result.scalar_one()
    assert referenced_table == "users"


async def test_check_period_rechaza_fecha_que_no_sea_el_dia_1_del_mes(rows):
    with pytest.raises(IntegrityError):
        await _insert_rent_period(rows, period=date(2026, 8, 15))


@pytest.mark.parametrize("table", ["rent_periods", "payments"])
async def test_ca_20_tabla_tiene_rls_habilitado_y_forzado(table: str):
    """RN-D01: RLS + FORCE en ambas tablas de la Capa 4."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :table"
            ),
            {"table": table},
        )
        row = result.one()
    assert row.relrowsecurity is True
    assert row.relforcerowsecurity is True


@pytest.mark.parametrize("table", ["rent_periods", "payments"])
async def test_ca_20_politica_tenant_isolation_usa_nullif_en_el_cast(table: str):
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT pg_get_expr(polqual, polrelid) FROM pg_policy "
                "WHERE polrelid = to_regclass(:table)"
            ),
            {"table": table},
        )
        qual = result.scalar_one()
    assert "NULLIF" in qual
    assert "app.current_tenant_id" in qual


# ─── CA-20-03: indices del spec ────────────────────────────────────────────


async def test_ca_20_03_indice_organization_period_existe_en_rent_periods():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE tablename = 'rent_periods'")
        )
        defs = [row[0] for row in result]
    assert any("organization_id" in d and "period" in d and "status" not in d for d in defs)


async def test_ca_20_03_indice_org_status_not_paid_existe_en_rent_periods():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE tablename = 'rent_periods'")
        )
        defs = [row[0] for row in result]
    assert any("organization_id" in d and "status" in d and "status <> 'paid'" in d for d in defs)


async def test_ca_20_03_indice_contract_period_existe_via_unique_constraint():
    """El UNIQUE (contract_id, period) crea su propio indice compuesto --
    satisface la recomendacion "CREATE INDEX ON rent_periods (contract_id,
    period)" del spec sin duplicar un indice manual redundante."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE tablename = 'rent_periods'")
        )
        defs = [row[0] for row in result]
    assert any("contract_id" in d and "period" in d and "UNIQUE" in d for d in defs)


async def test_ca_20_03_indice_rent_period_id_not_voided_existe_en_payments():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE tablename = 'payments'")
        )
        defs = [row[0] for row in result]
    assert any("rent_period_id" in d and "voided_at IS NULL" in d for d in defs)


async def test_ca_20_03_indice_organization_payment_date_existe_en_payments():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE tablename = 'payments'")
        )
        defs = [row[0] for row in result]
    assert any("organization_id" in d and "payment_date" in d for d in defs)


# ─── CA-20-01: UNIQUE (contract_id, period) — RN-P01 ───────────────────────


class TestCA2001UniqueContractPeriod:
    """CA-20-01: existe un UNIQUE (contract_id, period), probado."""

    async def test_ca_20_01_rejects_duplicate_period_for_same_contract(self, rows):
        await _insert_rent_period(rows, period=date(2026, 8, 1))
        with pytest.raises(IntegrityError):
            await _insert_rent_period(rows, period=date(2026, 8, 1))

    async def test_ca_20_01_allows_different_periods_for_same_contract(self, rows):
        await _insert_rent_period(rows, period=date(2026, 8, 1))
        second_id = await _insert_rent_period(rows, period=date(2026, 9, 1))
        assert second_id is not None

    async def test_ca_20_01_allows_same_period_for_different_contracts(self, rows):
        await _insert_rent_period(rows, contract_id=rows.contract_id, period=date(2026, 8, 1))
        second_id = await _insert_rent_period(
            rows, contract_id=rows.contract_b_id, period=date(2026, 8, 1)
        )
        assert second_id is not None


# ─── CA-20-02: CHECK paid_total <= amount_due ──────────────────────────────


class TestCA2002PaidTotalCheck:
    """CA-20-02: existe un CHECK que impide paid_total > amount_due, probado."""

    async def test_ca_20_02_rejects_paid_total_greater_than_amount_due(self, rows):
        with pytest.raises(IntegrityError):
            await _insert_rent_period(rows, amount_due="100000.00", paid_total="100000.01")

    async def test_ca_20_02_allows_paid_total_equal_to_amount_due(self, rows):
        rent_period_id = await _insert_rent_period(
            rows, amount_due="100000.00", paid_total="100000.00"
        )
        assert rent_period_id is not None

    async def test_ca_20_02_allows_paid_total_less_than_amount_due(self, rows):
        rent_period_id = await _insert_rent_period(
            rows, amount_due="100000.00", paid_total="50000.00"
        )
        assert rent_period_id is not None

    async def test_ca_20_02_rejects_update_that_pushes_paid_total_over_amount_due(self, rows):
        rent_period_id = await _insert_rent_period(
            rows, amount_due="100000.00", paid_total="50000.00"
        )
        session_factory = get_session_factory()
        with pytest.raises(IntegrityError):
            async with session_factory() as session, session.begin():
                # issue #42: no se testea tenant isolation aca -- bypass RLS.
                await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
                await session.execute(
                    sa.text("UPDATE rent_periods SET paid_total = 150000.00 WHERE id = :id"),
                    {"id": str(rent_period_id)},
                )


# ─── payments: CHECKs propios (amount > 0, exchange_rate > 0, enums) ───────


class TestPaymentsChecks:
    """Checks propios de `payments` documentados en el spec (sdd_02 §2.10)."""

    async def test_rejects_amount_not_greater_than_zero(self, rows):
        rent_period_id = await _insert_rent_period(rows)
        with pytest.raises(IntegrityError):
            await _insert_payment(rows, rent_period_id=rent_period_id, amount="0")

    async def test_rejects_negative_amount(self, rows):
        rent_period_id = await _insert_rent_period(rows)
        with pytest.raises(IntegrityError):
            await _insert_payment(rows, rent_period_id=rent_period_id, amount="-1")

    async def test_allows_null_exchange_rate(self, rows):
        rent_period_id = await _insert_rent_period(rows)
        payment_id = await _insert_payment(rows, rent_period_id=rent_period_id, exchange_rate=None)
        assert payment_id is not None

    async def test_rejects_exchange_rate_not_greater_than_zero(self, rows):
        rent_period_id = await _insert_rent_period(rows)
        with pytest.raises(IntegrityError):
            await _insert_payment(rows, rent_period_id=rent_period_id, exchange_rate="0")

    async def test_allows_positive_exchange_rate(self, rows):
        rent_period_id = await _insert_rent_period(rows)
        payment_id = await _insert_payment(
            rows,
            rent_period_id=rent_period_id,
            payment_currency="USD",
            exchange_rate="1350.5000",
        )
        assert payment_id is not None

    async def test_rejects_invalid_method(self, rows):
        rent_period_id = await _insert_rent_period(rows)
        with pytest.raises(IntegrityError):
            await _insert_payment(rows, rent_period_id=rent_period_id, method="check")

    async def test_rejects_invalid_destination(self, rows):
        rent_period_id = await _insert_rent_period(rows)
        with pytest.raises(IntegrityError):
            await _insert_payment(rows, rent_period_id=rent_period_id, destination="cash_box")

    async def test_rejects_invalid_payment_currency(self, rows):
        rent_period_id = await _insert_rent_period(rows)
        with pytest.raises(IntegrityError):
            await _insert_payment(rows, rent_period_id=rent_period_id, payment_currency="EUR")

    async def test_allows_voiding_a_payment_with_voided_at_and_voided_by(self, rows):
        """RN-D04: la anulacion logica se registra con autor."""
        rent_period_id = await _insert_rent_period(rows)
        payment_id = await _insert_payment(rows, rent_period_id=rent_period_id)
        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            # issue #42: no se testea tenant isolation aca -- bypass RLS.
            await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
            await session.execute(
                sa.text(
                    "UPDATE payments SET voided_at = now(), voided_by = :user_id WHERE id = :id"
                ),
                {"id": str(payment_id), "user_id": str(rows.user_id)},
            )
        async with session_factory() as session:
            await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
            result = await session.execute(
                sa.text("SELECT voided_at, voided_by FROM payments WHERE id = :id"),
                {"id": str(payment_id)},
            )
            voided_at, voided_by = result.one()
        assert voided_at is not None
        assert str(voided_by) == str(rows.user_id)
