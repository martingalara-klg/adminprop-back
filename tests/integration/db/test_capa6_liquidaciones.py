"""Issue #27 — Migracion Capa 6: recurring_charges + charge_entries +
settlements + settlement_line_items (+ALTER work_orders), schema/RLS/
CHECK/UNIQUE/indices.

Requiere Postgres real con `alembic upgrade head` ya corrido -- mismo
patron que `tests/integration/db/test_capa5_mantenimiento.py` (issue #25).

SDD: infrastructure/spec_data_model.md §Capa 6 — Liquidaciones
Implements: CA-27-01 (UNIQUE (landlord_id, period) en settlements,
            probado), CA-27-02 (UNIQUE (recurring_charge_id, period) en
            charge_entries, probado), CA-27-03 (ALTER de work_orders que
            agrega settled_in_settlement_id, FK a settlements, probado)
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

_RECURRING_CHARGES_COLUMNS = {
    "id",
    "organization_id",
    "property_id",
    "charge_type",
    "label",
    "is_active",
    "created_at",
    "updated_at",
    "deleted_at",
}

_CHARGE_ENTRIES_COLUMNS = {
    "id",
    "organization_id",
    "recurring_charge_id",
    "period",
    "amount",
    "notes",
    "created_by",
    "created_at",
    "updated_at",
}

_SETTLEMENTS_COLUMNS = {
    "id",
    "organization_id",
    "landlord_id",
    "period",
    "status",
    "exchange_rate",
    "total_collected",
    "commission_total",
    "charges_total",
    "repairs_total",
    "already_settled_total",
    "net_amount",
    "commission_pct_used",
    "regenerated_count",
    "generated_by",
    "issued_at",
    "created_at",
    "updated_at",
}

_SETTLEMENT_LINE_ITEMS_COLUMNS = {
    "id",
    "organization_id",
    "settlement_id",
    "line_type",
    "property_id",
    "source_entity_type",
    "source_entity_id",
    "original_amount",
    "original_currency",
    "amount_ars",
    "description",
    "created_at",
}

_PERIOD = date(2026, 8, 1)


class _Rows:
    def __init__(
        self,
        *,
        organization_id: uuid.UUID,
        landlord_id: uuid.UUID,
        property_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        self.organization_id = organization_id
        self.landlord_id = landlord_id
        self.property_id = property_id
        self.user_id = user_id


@pytest.fixture
async def rows() -> AsyncGenerator[_Rows]:
    """Siembra una organizacion con landlord + property + un usuario --
    suficiente para ejercer los constraints de `recurring_charges` /
    `charge_entries` / `settlements` / `settlement_line_items` (mismo
    criterio que `tests/integration/db/test_capa5_mantenimiento.py`)."""
    session_factory = get_session_factory()
    org_id = uuid.uuid4()
    landlord_id = uuid.uuid4()
    property_id = uuid.uuid4()
    user_id = uuid.uuid4()

    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text(
                "INSERT INTO organizations (id, slug, name) VALUES (:id, :slug, 'Org Liquidaciones')"
            ),
            {"id": str(org_id), "slug": f"org-{org_id.hex[:8]}"},
        )
        await session.execute(
            sa.text(
                "INSERT INTO users (id, email, password_hash, full_name) "
                "VALUES (:id, :email, 'hash', 'Operador Liquidaciones')"
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
                "VALUES (:id, :org_id, :landlord_id, 'Propiedad Liquidaciones')"
            ),
            {"id": str(property_id), "org_id": str(org_id), "landlord_id": str(landlord_id)},
        )

    yield _Rows(
        organization_id=org_id, landlord_id=landlord_id, property_id=property_id, user_id=user_id
    )

    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text(
                "DELETE FROM settlement_line_items WHERE organization_id = :org_id"
            ),
            {"org_id": str(org_id)},
        )
        await session.execute(
            sa.text(
                "UPDATE work_orders SET settled_in_settlement_id = NULL WHERE organization_id = :org_id"
            ),
            {"org_id": str(org_id)},
        )
        await session.execute(
            sa.text("DELETE FROM settlements WHERE organization_id = :org_id"),
            {"org_id": str(org_id)},
        )
        await session.execute(
            sa.text("DELETE FROM charge_entries WHERE organization_id = :org_id"),
            {"org_id": str(org_id)},
        )
        await session.execute(
            sa.text("DELETE FROM recurring_charges WHERE organization_id = :org_id"),
            {"org_id": str(org_id)},
        )
        await session.execute(
            sa.text("DELETE FROM work_orders WHERE organization_id = :org_id"),
            {"org_id": str(org_id)},
        )
        await session.execute(
            sa.text("DELETE FROM properties WHERE organization_id = :org_id"),
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


async def _insert_recurring_charge(
    rows: _Rows,
    *,
    charge_type: str = "rentas",
    label: str = "Rentas Cordoba",
) -> uuid.UUID:
    recurring_charge_id = uuid.uuid4()
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text(
                "INSERT INTO recurring_charges "
                "(id, organization_id, property_id, charge_type, label) "
                "VALUES (:id, :org_id, :property_id, :charge_type, :label)"
            ),
            {
                "id": str(recurring_charge_id),
                "org_id": str(rows.organization_id),
                "property_id": str(rows.property_id),
                "charge_type": charge_type,
                "label": label,
            },
        )
    return recurring_charge_id


async def _insert_charge_entry(
    rows: _Rows,
    *,
    recurring_charge_id: uuid.UUID,
    period: date = _PERIOD,
    amount: str = "15000.00",
) -> uuid.UUID:
    entry_id = uuid.uuid4()
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text(
                "INSERT INTO charge_entries "
                "(id, organization_id, recurring_charge_id, period, amount, created_by) "
                "VALUES (:id, :org_id, :recurring_charge_id, :period, :amount, :created_by)"
            ),
            {
                "id": str(entry_id),
                "org_id": str(rows.organization_id),
                "recurring_charge_id": str(recurring_charge_id),
                "period": period,
                "amount": amount,
                "created_by": str(rows.user_id),
            },
        )
    return entry_id


async def _insert_settlement(
    rows: _Rows,
    *,
    period: date = _PERIOD,
    status: str = "draft",
    commission_pct_used: str = "10.0000",
) -> uuid.UUID:
    settlement_id = uuid.uuid4()
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text(
                "INSERT INTO settlements "
                "(id, organization_id, landlord_id, period, status, commission_pct_used, generated_by) "
                "VALUES (:id, :org_id, :landlord_id, :period, :status, :commission_pct_used, :generated_by)"
            ),
            {
                "id": str(settlement_id),
                "org_id": str(rows.organization_id),
                "landlord_id": str(rows.landlord_id),
                "period": period,
                "status": status,
                "commission_pct_used": commission_pct_used,
                "generated_by": str(rows.user_id),
            },
        )
    return settlement_id


async def _insert_line_item(
    rows: _Rows,
    *,
    settlement_id: uuid.UUID,
    line_type: str = "rent_collected",
    original_currency: str = "ARS",
    original_amount: str = "100000.00",
    amount_ars: str = "100000.00",
) -> uuid.UUID:
    line_item_id = uuid.uuid4()
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text(
                "INSERT INTO settlement_line_items "
                "(id, organization_id, settlement_id, line_type, original_amount, "
                "original_currency, amount_ars) "
                "VALUES (:id, :org_id, :settlement_id, :line_type, :original_amount, "
                ":original_currency, :amount_ars)"
            ),
            {
                "id": str(line_item_id),
                "org_id": str(rows.organization_id),
                "settlement_id": str(settlement_id),
                "line_type": line_type,
                "original_amount": original_amount,
                "original_currency": original_currency,
                "amount_ars": amount_ars,
            },
        )
    return line_item_id


async def _insert_work_order(rows: _Rows) -> uuid.UUID:
    work_order_id = uuid.uuid4()
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text(
                "INSERT INTO work_orders "
                "(id, organization_id, property_id, title, payer, created_by) "
                "VALUES (:id, :org_id, :property_id, 'Reparacion liquidada', 'landlord', :created_by)"
            ),
            {
                "id": str(work_order_id),
                "org_id": str(rows.organization_id),
                "property_id": str(rows.property_id),
                "created_by": str(rows.user_id),
            },
        )
    return work_order_id


# ─── Schema identico al spec ────────────────────────────────────────────────


async def test_ca_27_recurring_charges_columnas_identicas_al_spec():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'recurring_charges'"
            )
        )
        columns = {row[0] for row in result}
    assert columns == _RECURRING_CHARGES_COLUMNS


async def test_ca_27_charge_entries_columnas_identicas_al_spec():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'charge_entries'"
            )
        )
        columns = {row[0] for row in result}
    assert columns == _CHARGE_ENTRIES_COLUMNS


async def test_ca_27_settlements_columnas_identicas_al_spec():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'settlements'"
            )
        )
        columns = {row[0] for row in result}
    assert columns == _SETTLEMENTS_COLUMNS


async def test_ca_27_settlement_line_items_columnas_identicas_al_spec():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'settlement_line_items'"
            )
        )
        columns = {row[0] for row in result}
    assert columns == _SETTLEMENT_LINE_ITEMS_COLUMNS


async def test_ca_27_03_work_orders_tiene_settled_in_settlement_id():
    """CA-27-03: el ALTER agrego la columna a work_orders."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'work_orders' AND column_name = 'settled_in_settlement_id'"
            )
        )
        columns = {row[0] for row in result}
    assert columns == {"settled_in_settlement_id"}


async def test_ca_27_03_fk_work_orders_settled_in_settlement_id_referencia_settlements():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT confrelid::regclass::text FROM pg_constraint "
                "WHERE conrelid = 'work_orders'::regclass AND contype = 'f' "
                "AND conname LIKE '%settled_in_settlement_id%'"
            )
        )
        referenced_table = result.scalar_one()
    assert referenced_table == "settlements"


async def test_ca_27_fk_recurring_charges_property_id_referencia_properties():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT confrelid::regclass::text FROM pg_constraint "
                "WHERE conrelid = 'recurring_charges'::regclass AND contype = 'f' "
                "AND conname LIKE '%property_id%'"
            )
        )
        referenced_table = result.scalar_one()
    assert referenced_table == "properties"


async def test_ca_27_fk_charge_entries_recurring_charge_id_referencia_recurring_charges():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT confrelid::regclass::text FROM pg_constraint "
                "WHERE conrelid = 'charge_entries'::regclass AND contype = 'f' "
                "AND conname LIKE '%recurring_charge_id%'"
            )
        )
        referenced_table = result.scalar_one()
    assert referenced_table == "recurring_charges"


async def test_ca_27_fk_settlements_landlord_id_referencia_landlords():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT confrelid::regclass::text FROM pg_constraint "
                "WHERE conrelid = 'settlements'::regclass AND contype = 'f' "
                "AND conname LIKE '%landlord_id%'"
            )
        )
        referenced_table = result.scalar_one()
    assert referenced_table == "landlords"


async def test_ca_27_fk_settlement_line_items_settlement_id_referencia_settlements():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT confrelid::regclass::text FROM pg_constraint "
                "WHERE conrelid = 'settlement_line_items'::regclass AND contype = 'f' "
                "AND conname LIKE '%settlement_id%'"
            )
        )
        referenced_table = result.scalar_one()
    assert referenced_table == "settlements"


async def test_ca_27_settlement_line_items_source_entity_id_no_tiene_fk_fisica():
    """source_entity_id es polimorfica -- integridad app-level, sin FK."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conrelid = 'settlement_line_items'::regclass AND contype = 'f' "
                "AND conname LIKE '%source_entity_id%'"
            )
        )
        count = result.scalar_one()
    assert count == 0


@pytest.mark.parametrize(
    "table",
    ["recurring_charges", "charge_entries", "settlements", "settlement_line_items"],
)
async def test_ca_27_tabla_tiene_rls_habilitado_y_forzado(table: str):
    """RN-D01: RLS + FORCE en las cuatro tablas de la Capa 6."""
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


@pytest.mark.parametrize(
    "table",
    ["recurring_charges", "charge_entries", "settlements", "settlement_line_items"],
)
async def test_ca_27_politica_tenant_isolation_usa_nullif_en_el_cast(table: str):
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


# ─── Indices del spec ───────────────────────────────────────────────────────


async def test_ca_27_indice_settlement_id_existe_en_settlement_line_items():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE tablename = 'settlement_line_items'")
        )
        defs = [row[0] for row in result]
    assert any("settlement_id" in d for d in defs)


async def test_ca_27_indice_organization_period_existe_en_charge_entries():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE tablename = 'charge_entries'")
        )
        defs = [row[0] for row in result]
    assert any("organization_id" in d and "period" in d for d in defs)


async def test_ca_27_indice_organization_id_existe_en_recurring_charges():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE tablename = 'recurring_charges'")
        )
        defs = [row[0] for row in result]
    assert any("organization_id" in d for d in defs)


# ─── CA-27-01: UNIQUE (landlord_id, period) en settlements ─────────────────


class TestCA2701UniqueLandlordPeriod:
    """CA-27-01: existe UNIQUE (landlord_id, period) en settlements,
    probado end-to-end contra Postgres real."""

    async def test_ca_27_01_allows_first_settlement_for_landlord_and_period(self, rows):
        settlement_id = await _insert_settlement(rows, period=_PERIOD)
        assert settlement_id is not None

    async def test_ca_27_01_rejects_second_settlement_for_same_landlord_and_period(self, rows):
        await _insert_settlement(rows, period=_PERIOD)
        with pytest.raises(IntegrityError):
            await _insert_settlement(rows, period=_PERIOD)

    async def test_ca_27_01_allows_same_landlord_different_period(self, rows):
        first = await _insert_settlement(rows, period=date(2026, 8, 1))
        second = await _insert_settlement(rows, period=date(2026, 9, 1))
        assert first is not None
        assert second is not None


# ─── CA-27-02: UNIQUE (recurring_charge_id, period) en charge_entries ──────


class TestCA2702UniqueRecurringChargePeriod:
    """CA-27-02: existe UNIQUE (recurring_charge_id, period) en
    charge_entries, probado end-to-end contra Postgres real."""

    async def test_ca_27_02_allows_first_entry_for_charge_and_period(self, rows):
        recurring_charge_id = await _insert_recurring_charge(rows)
        entry_id = await _insert_charge_entry(
            rows, recurring_charge_id=recurring_charge_id, period=_PERIOD
        )
        assert entry_id is not None

    async def test_ca_27_02_rejects_second_entry_for_same_charge_and_period(self, rows):
        recurring_charge_id = await _insert_recurring_charge(rows)
        await _insert_charge_entry(rows, recurring_charge_id=recurring_charge_id, period=_PERIOD)
        with pytest.raises(IntegrityError):
            await _insert_charge_entry(
                rows, recurring_charge_id=recurring_charge_id, period=_PERIOD
            )

    async def test_ca_27_02_allows_same_charge_different_period(self, rows):
        recurring_charge_id = await _insert_recurring_charge(rows)
        first = await _insert_charge_entry(
            rows, recurring_charge_id=recurring_charge_id, period=date(2026, 8, 1)
        )
        second = await _insert_charge_entry(
            rows, recurring_charge_id=recurring_charge_id, period=date(2026, 9, 1)
        )
        assert first is not None
        assert second is not None

    async def test_ca_27_02_allows_different_charges_same_period(self, rows):
        charge_a = await _insert_recurring_charge(rows, label="Rentas")
        charge_b = await _insert_recurring_charge(rows, label="Municipalidad")
        first = await _insert_charge_entry(rows, recurring_charge_id=charge_a, period=_PERIOD)
        second = await _insert_charge_entry(rows, recurring_charge_id=charge_b, period=_PERIOD)
        assert first is not None
        assert second is not None


# ─── CA-27-03: ALTER work_orders + settled_in_settlement_id ────────────────


class TestCA2703WorkOrdersSettledInSettlement:
    """CA-27-03: `work_orders` tiene el ALTER que agrega
    `settled_in_settlement_id`, ejercido end-to-end (RN-L04)."""

    async def test_ca_27_03_allows_linking_work_order_to_settlement(self, rows):
        work_order_id = await _insert_work_order(rows)
        settlement_id = await _insert_settlement(rows)
        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await session.execute(
                sa.text(
                    "UPDATE work_orders SET settled_in_settlement_id = :settlement_id "
                    "WHERE id = :id"
                ),
                {"settlement_id": str(settlement_id), "id": str(work_order_id)},
            )
        async with session_factory() as session:
            result = await session.execute(
                sa.text("SELECT settled_in_settlement_id FROM work_orders WHERE id = :id"),
                {"id": str(work_order_id)},
            )
            settled_in_settlement_id = result.scalar_one()
        assert str(settled_in_settlement_id) == str(settlement_id)

    async def test_ca_27_03_allows_work_order_without_settlement_link(self, rows):
        work_order_id = await _insert_work_order(rows)
        session_factory = get_session_factory()
        async with session_factory() as session:
            result = await session.execute(
                sa.text("SELECT settled_in_settlement_id FROM work_orders WHERE id = :id"),
                {"id": str(work_order_id)},
            )
            settled_in_settlement_id = result.scalar_one()
        assert settled_in_settlement_id is None

    async def test_ca_27_03_rejects_link_to_nonexistent_settlement(self, rows):
        work_order_id = await _insert_work_order(rows)
        session_factory = get_session_factory()
        with pytest.raises(IntegrityError):
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "UPDATE work_orders SET settled_in_settlement_id = :settlement_id "
                        "WHERE id = :id"
                    ),
                    {"settlement_id": str(uuid.uuid4()), "id": str(work_order_id)},
                )


# ─── Checks propios de recurring_charges / charge_entries / settlements /
# settlement_line_items ──────────────────────────────────────────────────


class TestRecurringChargesChecks:
    async def test_rejects_invalid_charge_type(self, rows):
        with pytest.raises(IntegrityError):
            await _insert_recurring_charge(rows, charge_type="agua")

    async def test_allows_each_documented_charge_type(self, rows):
        for charge_type in ("rentas", "municipalidad", "otro"):
            recurring_charge_id = await _insert_recurring_charge(rows, charge_type=charge_type)
            assert recurring_charge_id is not None

    async def test_is_active_default_is_true(self, rows):
        recurring_charge_id = await _insert_recurring_charge(rows)
        session_factory = get_session_factory()
        async with session_factory() as session:
            result = await session.execute(
                sa.text("SELECT is_active FROM recurring_charges WHERE id = :id"),
                {"id": str(recurring_charge_id)},
            )
            is_active = result.scalar_one()
        assert is_active is True

    async def test_allows_soft_deleting_via_deleted_at(self, rows):
        """Apendice B: recurring_charges usa deleted_at (RN-D02)."""
        recurring_charge_id = await _insert_recurring_charge(rows)
        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await session.execute(
                sa.text("UPDATE recurring_charges SET deleted_at = now() WHERE id = :id"),
                {"id": str(recurring_charge_id)},
            )
        async with session_factory() as session:
            result = await session.execute(
                sa.text("SELECT deleted_at FROM recurring_charges WHERE id = :id"),
                {"id": str(recurring_charge_id)},
            )
            deleted_at = result.scalar_one()
        assert deleted_at is not None


class TestChargeEntriesChecks:
    async def test_rejects_negative_amount(self, rows):
        recurring_charge_id = await _insert_recurring_charge(rows)
        with pytest.raises(IntegrityError):
            await _insert_charge_entry(rows, recurring_charge_id=recurring_charge_id, amount="-1")

    async def test_allows_zero_amount(self, rows):
        recurring_charge_id = await _insert_recurring_charge(rows)
        entry_id = await _insert_charge_entry(
            rows, recurring_charge_id=recurring_charge_id, amount="0"
        )
        assert entry_id is not None

    async def test_rejects_period_not_normalized_to_first_of_month(self, rows):
        recurring_charge_id = await _insert_recurring_charge(rows)
        with pytest.raises(IntegrityError):
            await _insert_charge_entry(
                rows, recurring_charge_id=recurring_charge_id, period=date(2026, 8, 15)
            )


class TestSettlementsChecks:
    async def test_rejects_invalid_status(self, rows):
        with pytest.raises(IntegrityError):
            await _insert_settlement(rows, status="pending")

    async def test_allows_each_documented_status(self, rows):
        for index, status in enumerate(("draft", "issued")):
            settlement_id = await _insert_settlement(
                rows, period=date(2026, 8 + index, 1), status=status
            )
            assert settlement_id is not None

    async def test_status_default_is_draft(self, rows):
        settlement_id = uuid.uuid4()
        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await session.execute(
                sa.text(
                    "INSERT INTO settlements "
                    "(id, organization_id, landlord_id, period, commission_pct_used, generated_by) "
                    "VALUES (:id, :org_id, :landlord_id, :period, 10, :generated_by)"
                ),
                {
                    "id": str(settlement_id),
                    "org_id": str(rows.organization_id),
                    "landlord_id": str(rows.landlord_id),
                    "period": _PERIOD,
                    "generated_by": str(rows.user_id),
                },
            )
        async with session_factory() as session:
            result = await session.execute(
                sa.text("SELECT status FROM settlements WHERE id = :id"),
                {"id": str(settlement_id)},
            )
            status = result.scalar_one()
        assert status == "draft"

    async def test_rejects_exchange_rate_not_greater_than_zero(self, rows):
        settlement_id = uuid.uuid4()
        session_factory = get_session_factory()
        with pytest.raises(IntegrityError):
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO settlements "
                        "(id, organization_id, landlord_id, period, commission_pct_used, "
                        "exchange_rate, generated_by) "
                        "VALUES (:id, :org_id, :landlord_id, :period, 10, 0, :generated_by)"
                    ),
                    {
                        "id": str(settlement_id),
                        "org_id": str(rows.organization_id),
                        "landlord_id": str(rows.landlord_id),
                        "period": _PERIOD,
                        "generated_by": str(rows.user_id),
                    },
                )

    async def test_allows_null_exchange_rate(self, rows):
        settlement_id = await _insert_settlement(rows)
        session_factory = get_session_factory()
        async with session_factory() as session:
            result = await session.execute(
                sa.text("SELECT exchange_rate FROM settlements WHERE id = :id"),
                {"id": str(settlement_id)},
            )
            exchange_rate = result.scalar_one()
        assert exchange_rate is None

    async def test_rejects_period_not_normalized_to_first_of_month(self, rows):
        with pytest.raises(IntegrityError):
            await _insert_settlement(rows, period=date(2026, 8, 15))


class TestSettlementLineItemsChecks:
    async def test_rejects_invalid_line_type(self, rows):
        settlement_id = await _insert_settlement(rows)
        with pytest.raises(IntegrityError):
            await _insert_line_item(rows, settlement_id=settlement_id, line_type="bonus")

    async def test_allows_each_documented_line_type(self, rows):
        settlement_id = await _insert_settlement(rows)
        for line_type in (
            "rent_collected",
            "commission",
            "tax_charge",
            "repair",
            "already_settled",
        ):
            line_item_id = await _insert_line_item(
                rows, settlement_id=settlement_id, line_type=line_type
            )
            assert line_item_id is not None

    async def test_rejects_invalid_original_currency(self, rows):
        settlement_id = await _insert_settlement(rows)
        with pytest.raises(IntegrityError):
            await _insert_line_item(rows, settlement_id=settlement_id, original_currency="EUR")

    async def test_allows_each_documented_original_currency(self, rows):
        settlement_id = await _insert_settlement(rows)
        for currency in ("ARS", "USD"):
            line_item_id = await _insert_line_item(
                rows, settlement_id=settlement_id, original_currency=currency
            )
            assert line_item_id is not None
