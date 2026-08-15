"""Issue #12 — Migracion Capa 1: landlords + renters, schema/RLS/CHECK.

Requiere Postgres real con `alembic upgrade head` ya corrido -- mismo
patron que `tests/integration/db/test_capa0_fundacion.py` y
`tests/integration/db/test_audit_logs.py`.

SDD: infrastructure/spec_data_model.md §Capa 1 — Personas
     + core/sdd_04_nonfunctional.md §2.4
Implements: CA-12-01 (cifrado de bank_info verificable a nivel de DB),
            CA-12-02 (RLS + FORCE en ambas tablas),
            CA-12-03 (commission_pct CHECK 0-100)
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_engine

pytestmark = pytest.mark.asyncio

_LANDLORDS_COLUMNS = {
    "id",
    "organization_id",
    "name",
    "tax_id",
    "phone",
    "email",
    "bank_info",
    "commission_pct",
    "notes",
    "metadata",
    "created_at",
    "updated_at",
    "deleted_at",
}

_RENTERS_COLUMNS = {
    "id",
    "organization_id",
    "name",
    "tax_id",
    "phone",
    "email",
    "notes",
    "metadata",
    "created_at",
    "updated_at",
    "deleted_at",
}


async def test_ca_12_01_landlords_columnas_identicas_al_spec_salvo_bank_info_bytea():
    """spec_data_model.md §Capa 1 "landlords": columnas exactas, salvo la
    desviacion de tipo documentada en la migracion (bank_info BYTEA en vez
    de TEXT, para persistir el ciphertext de pgcrypto — sdd_04 §2.4)."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'landlords'"
            )
        )
        columns = {row[0] for row in result}
        result = await conn.execute(
            sa.text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'landlords' AND column_name = 'bank_info'"
            )
        )
        bank_info_type = result.scalar_one()
    assert columns == _LANDLORDS_COLUMNS
    assert bank_info_type == "bytea"


async def test_ca_12_03_commission_pct_tiene_check_de_rango_0_100():
    """CA-12-03: `commission_pct` rechaza valores fuera de [0, 100]."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'landlords'::regclass AND contype = 'c'"
            )
        )
        check_defs = [row[0] for row in result]
    assert any("commission_pct" in d and "100" in d for d in check_defs)


async def test_ca_12_01_renters_columnas_identicas_al_spec():
    """spec_data_model.md §Capa 1 "renters": columnas exactas (sin
    bank_info ni commission_pct — no se le rinde al inquilino)."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'renters'"
            )
        )
        columns = {row[0] for row in result}
    assert columns == _RENTERS_COLUMNS


@pytest.mark.parametrize("table", ["landlords", "renters"])
async def test_ca_12_02_tabla_tiene_rls_habilitado_y_forzado(table: str):
    """CA-12-02: RLS + FORCE en ambas tablas (patron de Capa 0/audit_logs)."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = :table"
            ),
            {"table": table},
        )
        row = result.one()
    assert row.relrowsecurity is True
    assert row.relforcerowsecurity is True


@pytest.mark.parametrize("table", ["landlords", "renters"])
async def test_ca_12_02_politica_tenant_isolation_usa_nullif_en_el_cast(table: str):
    """Mismo fix NULLIF que el resto de las tablas tenant-scoped (issue #3)."""
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


@pytest.mark.parametrize("table", ["landlords", "renters"])
async def test_ca_12_indices_organization_id_existen(table: str):
    """spec_data_model.md §"Indices PostgreSQL Recomendados": patron general
    `(organization_id) WHERE deleted_at IS NULL`."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE tablename = :table"),
            {"table": table},
        )
        defs = [row[0] for row in result]
    assert any(
        "organization_id" in d and "deleted_at IS NULL" in d for d in defs
    )
