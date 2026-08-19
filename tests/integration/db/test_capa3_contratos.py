"""Issue #16 — Migracion Capa 3: contracts + contract_adjustments,
schema/RLS/CHECK/EXCLUDE/indice parcial unico.

Requiere Postgres real con `alembic upgrade head` ya corrido -- mismo
patron que `tests/integration/db/test_capa2_propiedades.py` (issue #14).

SDD: infrastructure/spec_data_model.md §Capa 3 — Contratos
Implements: CA-16-01 (EXCLUDE + btree_gist probado con test dedicado),
            CA-16-02 (CHECK que impide ajuste en contratos USD),
            CA-16-03 (indice parcial unico: un solo `pending` por contrato)
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

_CONTRACTS_COLUMNS = {
    "id",
    "organization_id",
    "property_id",
    "renter_id",
    "currency",
    "initial_amount",
    "current_amount",
    "start_date",
    "end_date",
    "daily_late_fee_pct",
    "adjustment_frequency_months",
    "adjustment_index",
    "adjustment_index_notes",
    "status",
    "notes",
    "metadata",
    "created_at",
    "updated_at",
    "deleted_at",
    # issue #19 (migracion 20260819_123059): marca de idempotencia del
    # aviso de vencimiento, RF-05/CA-03-07.
    "expiring_notified_at",
}

_CONTRACT_ADJUSTMENTS_COLUMNS = {
    "id",
    "organization_id",
    "contract_id",
    "due_period",
    "status",
    "pct_applied",
    "previous_amount",
    "new_amount",
    "notes",
    "applied_by",
    "applied_at",
    "created_at",
    "updated_at",
}


class _Rows:
    def __init__(
        self,
        *,
        organization_id: uuid.UUID,
        property_id: uuid.UUID,
        property_b_id: uuid.UUID,
        renter_id: uuid.UUID,
    ) -> None:
        self.organization_id = organization_id
        self.property_id = property_id
        self.property_b_id = property_b_id
        self.renter_id = renter_id


@pytest.fixture
async def rows() -> AsyncGenerator[_Rows]:
    """Siembra una organizacion con un landlord, dos properties y un
    renter -- suficiente para ejercer los constraints de `contracts` sin
    depender de otro modulo (mismo criterio que
    `tests/integration/db/test_tenant_isolation_capa2.py`)."""
    session_factory = get_session_factory()
    org_id = uuid.uuid4()
    landlord_id = uuid.uuid4()
    property_id = uuid.uuid4()
    property_b_id = uuid.uuid4()
    renter_id = uuid.uuid4()

    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text(
                "INSERT INTO organizations (id, slug, name) VALUES (:id, :slug, 'Org Contratos')"
            ),
            {"id": str(org_id), "slug": f"org-{org_id.hex[:8]}"},
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
                "VALUES (:id_a, :org_id, :landlord_id, 'Propiedad A'), "
                "(:id_b, :org_id, :landlord_id, 'Propiedad B')"
            ),
            {
                "id_a": str(property_id),
                "id_b": str(property_b_id),
                "org_id": str(org_id),
                "landlord_id": str(landlord_id),
            },
        )
        await session.execute(
            sa.text(
                "INSERT INTO renters (id, organization_id, name) VALUES (:id, :org_id, 'Renter')"
            ),
            {"id": str(renter_id), "org_id": str(org_id)},
        )

    yield _Rows(
        organization_id=org_id,
        property_id=property_id,
        property_b_id=property_b_id,
        renter_id=renter_id,
    )

    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text("DELETE FROM contract_adjustments WHERE organization_id = :org_id"),
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
            sa.text("DELETE FROM organizations WHERE id = :org_id"),
            {"org_id": str(org_id)},
        )


async def _insert_contract(
    rows: _Rows,
    *,
    property_id: uuid.UUID | None = None,
    currency: str = "ARS",
    initial_amount: str = "100000.00",
    current_amount: str = "100000.00",
    start_date: date = date(2026, 1, 1),
    end_date: date = date(2026, 12, 31),
    daily_late_fee_pct: str = "0.5",
    adjustment_frequency_months: int | None = 3,
    adjustment_index: str | None = "icl",
    status: str = "active",
) -> uuid.UUID:
    contract_id = uuid.uuid4()
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text(
                "INSERT INTO contracts "
                "(id, organization_id, property_id, renter_id, currency, initial_amount, "
                "current_amount, start_date, end_date, daily_late_fee_pct, "
                "adjustment_frequency_months, adjustment_index, status) "
                "VALUES (:id, :org_id, :property_id, :renter_id, :currency, :initial_amount, "
                ":current_amount, :start_date, :end_date, :daily_late_fee_pct, "
                ":adjustment_frequency_months, :adjustment_index, :status)"
            ),
            {
                "id": str(contract_id),
                "org_id": str(rows.organization_id),
                "property_id": str(property_id or rows.property_id),
                "renter_id": str(rows.renter_id),
                "currency": currency,
                "initial_amount": initial_amount,
                "current_amount": current_amount,
                "start_date": start_date,
                "end_date": end_date,
                "daily_late_fee_pct": daily_late_fee_pct,
                "adjustment_frequency_months": adjustment_frequency_months,
                "adjustment_index": adjustment_index,
                "status": status,
            },
        )
    return contract_id


async def _insert_adjustment(
    rows: _Rows,
    *,
    contract_id: uuid.UUID,
    due_period: date = date(2026, 4, 1),
    status: str = "pending",
) -> uuid.UUID:
    adjustment_id = uuid.uuid4()
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text(
                "INSERT INTO contract_adjustments "
                "(id, organization_id, contract_id, due_period, status) "
                "VALUES (:id, :org_id, :contract_id, :due_period, :status)"
            ),
            {
                "id": str(adjustment_id),
                "org_id": str(rows.organization_id),
                "contract_id": str(contract_id),
                "due_period": due_period,
                "status": status,
            },
        )
    return adjustment_id


# ─── CA-16-01: schema identico al spec ────────────────────────────────────


async def test_ca_16_01_contracts_columnas_identicas_al_spec():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'contracts'"
            )
        )
        columns = {row[0] for row in result}
    assert columns == _CONTRACTS_COLUMNS


async def test_ca_16_01_contract_adjustments_columnas_identicas_al_spec():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'contract_adjustments'"
            )
        )
        columns = {row[0] for row in result}
    assert columns == _CONTRACT_ADJUSTMENTS_COLUMNS


async def test_ca_16_01_contracts_status_default_es_draft():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = 'contracts' AND column_name = 'status'"
            )
        )
        default = result.scalar_one()
    assert "draft" in default


async def test_ca_16_01_contract_adjustments_status_default_es_pending():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = 'contract_adjustments' AND column_name = 'status'"
            )
        )
        default = result.scalar_one()
    assert "pending" in default


async def test_ca_16_01_fk_contracts_property_id_referencia_properties():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT confrelid::regclass::text FROM pg_constraint "
                "WHERE conrelid = 'contracts'::regclass AND contype = 'f' "
                "AND conname LIKE '%property_id%'"
            )
        )
        referenced_table = result.scalar_one()
    assert referenced_table == "properties"


async def test_ca_16_01_fk_contracts_renter_id_referencia_renters():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT confrelid::regclass::text FROM pg_constraint "
                "WHERE conrelid = 'contracts'::regclass AND contype = 'f' "
                "AND conname LIKE '%renter_id%'"
            )
        )
        referenced_table = result.scalar_one()
    assert referenced_table == "renters"


async def test_ca_16_01_fk_contract_adjustments_contract_id_referencia_contracts():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT confrelid::regclass::text FROM pg_constraint "
                "WHERE conrelid = 'contract_adjustments'::regclass AND contype = 'f' "
                "AND conname LIKE '%contract_id%'"
            )
        )
        referenced_table = result.scalar_one()
    assert referenced_table == "contracts"


async def test_ca_16_01_check_end_date_debe_ser_posterior_a_start_date(rows):
    with pytest.raises(IntegrityError):
        await _insert_contract(rows, start_date=date(2026, 12, 31), end_date=date(2026, 1, 1))


@pytest.mark.parametrize("table", ["contracts", "contract_adjustments"])
async def test_ca_16_tabla_tiene_rls_habilitado_y_forzado(table: str):
    """RN-D01: RLS + FORCE en ambas tablas de la Capa 3."""
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


@pytest.mark.parametrize("table", ["contracts", "contract_adjustments"])
async def test_ca_16_politica_tenant_isolation_usa_nullif_en_el_cast(table: str):
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


async def test_ca_16_indice_organization_status_existe_en_contracts():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE tablename = 'contracts'")
        )
        defs = [row[0] for row in result]
    assert any("organization_id" in d and "status" in d and "end_date" not in d for d in defs)


async def test_ca_16_indice_org_end_date_para_vencimientos_existe_en_contracts():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE tablename = 'contracts'")
        )
        defs = [row[0] for row in result]
    assert any(
        "organization_id" in d and "end_date" in d and "status = 'active'" in d for d in defs
    )


async def test_ca_16_indice_property_id_existe_en_contracts_filtrado_por_deleted_at():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE tablename = 'contracts'")
        )
        defs = [row[0] for row in result]
    assert any("property_id" in d and "deleted_at IS NULL" in d for d in defs)


# ─── CA-16-01: EXCLUDE + btree_gist (RN-C01, no-solapamiento) ─────────────


class TestCA1601ContractsNoOverlap:
    """CA-16-01: el constraint de exclusion probado con un test dedicado."""

    async def test_rejects_overlapping_active_contracts_on_same_property(self, rows):
        await _insert_contract(
            rows, start_date=date(2026, 1, 1), end_date=date(2026, 12, 31), status="active"
        )
        with pytest.raises(IntegrityError):
            await _insert_contract(
                rows, start_date=date(2026, 6, 1), end_date=date(2027, 6, 1), status="active"
            )

    async def test_rejects_identical_date_range_on_same_property(self, rows):
        await _insert_contract(
            rows, start_date=date(2026, 1, 1), end_date=date(2026, 12, 31), status="active"
        )
        with pytest.raises(IntegrityError):
            await _insert_contract(
                rows, start_date=date(2026, 1, 1), end_date=date(2026, 12, 31), status="active"
            )

    async def test_allows_same_date_range_on_different_properties(self, rows):
        await _insert_contract(
            rows,
            property_id=rows.property_id,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status="active",
        )
        second_id = await _insert_contract(
            rows,
            property_id=rows.property_b_id,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status="active",
        )
        assert second_id is not None

    async def test_allows_overlapping_ranges_when_second_contract_is_draft(self, rows):
        await _insert_contract(
            rows, start_date=date(2026, 1, 1), end_date=date(2026, 12, 31), status="active"
        )
        second_id = await _insert_contract(
            rows, start_date=date(2026, 6, 1), end_date=date(2027, 6, 1), status="draft"
        )
        assert second_id is not None

    async def test_allows_overlapping_ranges_when_both_contracts_are_terminated(self, rows):
        await _insert_contract(
            rows, start_date=date(2026, 1, 1), end_date=date(2026, 12, 31), status="terminated"
        )
        second_id = await _insert_contract(
            rows, start_date=date(2026, 6, 1), end_date=date(2027, 6, 1), status="terminated"
        )
        assert second_id is not None

    async def test_allows_adjacent_non_overlapping_active_contracts_same_property(self, rows):
        await _insert_contract(
            rows, start_date=date(2026, 1, 1), end_date=date(2026, 6, 30), status="active"
        )
        second_id = await _insert_contract(
            rows, start_date=date(2026, 7, 1), end_date=date(2026, 12, 31), status="active"
        )
        assert second_id is not None


# ─── CA-16-02: CHECK que impide ajuste en contratos USD (RN-C02) ──────────


class TestCA1602UsdContractsCannotAdjust:
    """CA-16-02: existe un CHECK que impide ajuste en contratos USD."""

    async def test_ca_16_02_rejects_usd_contract_with_adjustment_frequency_months_set(self, rows):
        with pytest.raises(IntegrityError):
            await _insert_contract(
                rows,
                currency="USD",
                adjustment_frequency_months=3,
                adjustment_index=None,
            )

    async def test_ca_16_02_rejects_usd_contract_with_adjustment_index_set(self, rows):
        with pytest.raises(IntegrityError):
            await _insert_contract(
                rows,
                currency="USD",
                adjustment_frequency_months=None,
                adjustment_index="icl",
            )

    async def test_ca_16_02_allows_usd_contract_without_any_adjustment_field(self, rows):
        contract_id = await _insert_contract(
            rows,
            currency="USD",
            adjustment_frequency_months=None,
            adjustment_index=None,
        )
        assert contract_id is not None

    async def test_ca_16_02_allows_ars_contract_with_both_adjustment_fields_set(self, rows):
        contract_id = await _insert_contract(
            rows,
            currency="ARS",
            adjustment_frequency_months=3,
            adjustment_index="ipc_cordoba",
        )
        assert contract_id is not None

    async def test_ca_16_02_rejects_invalid_adjustment_index_value(self, rows):
        with pytest.raises(IntegrityError):
            await _insert_contract(rows, currency="ARS", adjustment_index="not_a_valid_index")


# ─── CA-16-03: indice parcial unico -- un solo `pending` por contrato ─────


class TestCA1603OnlyOnePendingAdjustmentPerContract:
    """CA-16-03: hay un indice parcial que garantiza un solo ajuste
    `pending` por contrato."""

    async def test_ca_16_03_rejects_second_pending_adjustment_for_same_contract(self, rows):
        contract_id = await _insert_contract(rows)
        await _insert_adjustment(rows, contract_id=contract_id, status="pending")
        with pytest.raises(IntegrityError):
            await _insert_adjustment(
                rows, contract_id=contract_id, due_period=date(2026, 5, 1), status="pending"
            )

    async def test_ca_16_03_allows_pending_adjustments_for_different_contracts(self, rows):
        contract_a = await _insert_contract(
            rows,
            property_id=rows.property_id,
            status="active",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 6, 30),
        )
        contract_b = await _insert_contract(
            rows,
            property_id=rows.property_b_id,
            status="active",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 6, 30),
        )
        await _insert_adjustment(rows, contract_id=contract_a, status="pending")
        second_id = await _insert_adjustment(rows, contract_id=contract_b, status="pending")
        assert second_id is not None

    async def test_ca_16_03_allows_a_new_pending_adjustment_after_the_previous_one_was_applied(
        self, rows
    ):
        contract_id = await _insert_contract(rows)
        first = await _insert_adjustment(rows, contract_id=contract_id, status="pending")

        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await session.execute(
                sa.text("UPDATE contract_adjustments SET status = 'applied' WHERE id = :id"),
                {"id": str(first)},
            )

        second_id = await _insert_adjustment(
            rows, contract_id=contract_id, due_period=date(2026, 5, 1), status="pending"
        )
        assert second_id is not None

    async def test_ca_16_03_allows_multiple_applied_adjustments_for_the_same_contract(self, rows):
        contract_id = await _insert_contract(rows)
        first = await _insert_adjustment(rows, contract_id=contract_id, status="applied")
        second = await _insert_adjustment(
            rows, contract_id=contract_id, due_period=date(2026, 5, 1), status="applied"
        )
        assert first != second


# ─── due_period normalizado al dia 1 del mes ──────────────────────────────


async def test_check_due_period_rechaza_fecha_que_no_sea_el_dia_1_del_mes(rows):
    contract_id = await _insert_contract(rows)
    with pytest.raises(IntegrityError):
        await _insert_adjustment(rows, contract_id=contract_id, due_period=date(2026, 4, 15))
