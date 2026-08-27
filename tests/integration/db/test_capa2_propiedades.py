"""Issue #14 — Migracion Capa 2: properties + property_service_accounts,
schema/RLS/CHECK/FK.

Requiere Postgres real con `alembic upgrade head` ya corrido -- mismo
patron que `tests/integration/db/test_capa1_personas.py` y
`tests/integration/db/test_audit_logs.py`.

SDD: infrastructure/spec_data_model.md §Capa 2 — Propiedades
Implements: CA-14-01 (tablas identicas al spec), CA-14-02 (RLS + FORCE en
            ambas tablas), CA-14-03 (indices declarados en el spec creados)
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_engine

pytestmark = pytest.mark.asyncio

_PROPERTIES_COLUMNS = {
    "id",
    "organization_id",
    "landlord_id",
    "neighborhood_id",
    "address",
    "property_type",
    "status",
    "notes",
    "metadata",
    "created_at",
    "updated_at",
    "deleted_at",
}

_PROPERTY_SERVICE_ACCOUNTS_COLUMNS = {
    "id",
    "organization_id",
    "property_id",
    "service_type",
    "account_number",
    "secondary_number",
    "notes",
    "created_at",
    "updated_at",
    "deleted_at",
}

_SERVICE_TYPES = ("rentas", "municipalidad", "luz", "gas", "agua", "expensas", "otro")


async def test_ca_14_01_properties_columnas_identicas_al_spec():
    """spec_data_model.md §Capa 2 "properties": columnas exactas."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'properties'"
            )
        )
        columns = {row[0] for row in result}
    assert columns == _PROPERTIES_COLUMNS


async def test_ca_14_01_property_service_accounts_columnas_identicas_al_spec():
    """spec_data_model.md §Capa 2 "property_service_accounts": columnas exactas."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'property_service_accounts'"
            )
        )
        columns = {row[0] for row in result}
    assert columns == _PROPERTY_SERVICE_ACCOUNTS_COLUMNS


async def test_ca_14_01_properties_status_default_es_available():
    """spec_data_model.md §Capa 2 "properties": `status` default `available`."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = 'properties' AND column_name = 'status'"
            )
        )
        default = result.scalar_one()
    assert "available" in default


async def test_ca_14_01_properties_property_type_default_es_departamento():
    """spec_data_model.md §Capa 2 "properties": `property_type` default `departamento`."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = 'properties' AND column_name = 'property_type'"
            )
        )
        default = result.scalar_one()
    assert "departamento" in default


async def test_ca_14_01_properties_landlord_id_es_not_null():
    """El body del issue exige `landlord_id` FK NOT NULL a `landlords`."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'properties' AND column_name = 'landlord_id'"
            )
        )
        is_nullable = result.scalar_one()
    assert is_nullable == "NO"


async def test_ca_14_01_property_service_accounts_secondary_number_es_nullable():
    """spec_data_model.md §Capa 2: `secondary_number` nullable (caso luz)."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'property_service_accounts' "
                "AND column_name = 'secondary_number'"
            )
        )
        is_nullable = result.scalar_one()
    assert is_nullable == "YES"


async def test_ca_14_check_properties_status_rechaza_valor_invalido():
    """CHECK: `properties.status` solo acepta available/rented/unavailable."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'properties'::regclass AND contype = 'c'"
            )
        )
        check_defs = [row[0] for row in result]
    assert any(
        "status" in d and "available" in d and "rented" in d and "unavailable" in d
        for d in check_defs
    )


async def test_ca_14_check_service_type_incluye_los_7_valores_del_spec():
    """CHECK: `property_service_accounts.service_type` incluye los 7 valores
    del spec (rentas, municipalidad, luz, gas, agua, expensas, otro)."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'property_service_accounts'::regclass AND contype = 'c'"
            )
        )
        check_defs = [row[0] for row in result]
    service_type_def = next(d for d in check_defs if "service_type" in d)
    for value in _SERVICE_TYPES:
        assert value in service_type_def


async def test_ca_14_fk_properties_landlord_id_referencia_landlords():
    """`properties.landlord_id` es FK a `landlords` (sin ON DELETE CASCADE
    -- el spec no lo declara para esta relacion)."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT confrelid::regclass::text FROM pg_constraint "
                "WHERE conrelid = 'properties'::regclass AND contype = 'f' "
                "AND conname LIKE '%landlord_id%'"
            )
        )
        referenced_table = result.scalar_one()
    assert referenced_table == "landlords"


async def test_ca_14_fk_property_service_accounts_property_id_referencia_properties():
    """`property_service_accounts.property_id` es FK NOT NULL a `properties`."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT confrelid::regclass::text FROM pg_constraint "
                "WHERE conrelid = 'property_service_accounts'::regclass AND contype = 'f' "
                "AND conname LIKE '%property_id%'"
            )
        )
        referenced_table = result.scalar_one()
    assert referenced_table == "properties"


@pytest.mark.parametrize("table", ["properties", "property_service_accounts"])
async def test_ca_14_02_tabla_tiene_rls_habilitado_y_forzado(table: str):
    """CA-14-02: RLS + FORCE en ambas tablas (patron de Capa 0/1/audit_logs)."""
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


@pytest.mark.parametrize("table", ["properties", "property_service_accounts"])
async def test_ca_14_02_politica_tenant_isolation_usa_nullif_en_el_cast(table: str):
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


async def test_ca_14_03_indice_organization_id_existe_en_properties():
    """spec_data_model.md §"Indices PostgreSQL Recomendados": patron general
    `(organization_id) WHERE deleted_at IS NULL`."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE tablename = 'properties'")
        )
        defs = [row[0] for row in result]
    assert any("organization_id" in d and "deleted_at IS NULL" in d for d in defs)


async def test_ca_14_03_indice_compuesto_org_property_existe_en_service_accounts():
    """Patron compuesto (organization_id, property_id) WHERE deleted_at IS NULL
    -- la consulta natural es "cuentas de servicio de esta propiedad"."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE tablename = 'property_service_accounts'")
        )
        defs = [row[0] for row in result]
    assert any(
        "organization_id" in d and "property_id" in d and "deleted_at IS NULL" in d for d in defs
    )
