"""Issue #27 — Aislamiento cross-tenant (RN-D01) sobre
`recurring_charges`/`charge_entries`/`settlements`/`settlement_line_items`.

Mismo patron que `tests/integration/db/test_tenant_isolation_capa5.py`
(issue #25): ejerce el aislamiento end-to-end con `set_tenant_context`,
no solo inspecciona el catalogo (eso lo cubre `test_capa6_liquidaciones.py`).

SDD: core/sdd_02_domain_model.md §3 RN-D01
     + docs/skills/tenant-isolation.md
Implements: CA-27 (RLS aisla cross-tenant en las cuatro tablas de la Capa 6)
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import date

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_session_factory, set_tenant_context

pytestmark = pytest.mark.asyncio

ORG_A = uuid.uuid4()
ORG_B = uuid.uuid4()

_TABLES = ("recurring_charges", "charge_entries", "settlements", "settlement_line_items")
_PERIOD = date(2026, 8, 1)


@pytest.fixture
async def two_orgs_with_settlement_rows_each() -> AsyncGenerator[None]:
    """Crea dos organizaciones, cada una con landlord + property + un
    usuario + un recurring_charge + un charge_entry + un settlement + un
    settlement_line_item (conexion por default: `adminprop`, superuser con
    BYPASSRLS -- setup ajeno al aislamiento que se quiere probar). Limpieza
    al final."""
    session_factory = get_session_factory()
    landlord_a, landlord_b = uuid.uuid4(), uuid.uuid4()
    property_a, property_b = uuid.uuid4(), uuid.uuid4()
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    recurring_charge_a, recurring_charge_b = uuid.uuid4(), uuid.uuid4()
    charge_entry_a, charge_entry_b = uuid.uuid4(), uuid.uuid4()
    settlement_a, settlement_b = uuid.uuid4(), uuid.uuid4()
    line_item_a, line_item_b = uuid.uuid4(), uuid.uuid4()

    async with session_factory() as session, session.begin():
        # issue #42: seed cruza dos organizaciones en una sola transaccion --
        # bypass RLS explicito, no se testea aislamiento en este bloque.
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        await session.execute(
            sa.text(
                "INSERT INTO organizations (id, slug, name) "
                "VALUES (:id_a, :slug_a, 'Org A'), (:id_b, :slug_b, 'Org B')"
            ),
            {
                "id_a": str(ORG_A),
                "slug_a": f"org-a-{ORG_A.hex[:8]}",
                "id_b": str(ORG_B),
                "slug_b": f"org-b-{ORG_B.hex[:8]}",
            },
        )
        await session.execute(
            sa.text(
                "INSERT INTO users (id, email, password_hash, full_name) "
                "VALUES (:id_a, :email_a, 'hash', 'Operador A'), "
                "(:id_b, :email_b, 'hash', 'Operador B')"
            ),
            {
                "id_a": str(user_a),
                "email_a": f"{user_a.hex[:8]}@adminprop.test",
                "id_b": str(user_b),
                "email_b": f"{user_b.hex[:8]}@adminprop.test",
            },
        )
        await session.execute(
            sa.text(
                "INSERT INTO landlords (id, organization_id, name, commission_pct) "
                "VALUES (:id_a, :org_a, 'Landlord A', 10), (:id_b, :org_b, 'Landlord B', 10)"
            ),
            {
                "id_a": str(landlord_a),
                "org_a": str(ORG_A),
                "id_b": str(landlord_b),
                "org_b": str(ORG_B),
            },
        )
        await session.execute(
            sa.text(
                "INSERT INTO properties (id, organization_id, landlord_id, address) "
                "VALUES (:id_a, :org_a, :landlord_a, 'Calle A 123'), "
                "(:id_b, :org_b, :landlord_b, 'Calle B 456')"
            ),
            {
                "id_a": str(property_a),
                "org_a": str(ORG_A),
                "landlord_a": str(landlord_a),
                "id_b": str(property_b),
                "org_b": str(ORG_B),
                "landlord_b": str(landlord_b),
            },
        )
        await session.execute(
            sa.text(
                "INSERT INTO recurring_charges "
                "(id, organization_id, property_id, charge_type, label) "
                "VALUES "
                "(:id_a, :org_a, :property_a, 'rentas', 'Rentas A'), "
                "(:id_b, :org_b, :property_b, 'rentas', 'Rentas B')"
            ),
            {
                "id_a": str(recurring_charge_a),
                "org_a": str(ORG_A),
                "property_a": str(property_a),
                "id_b": str(recurring_charge_b),
                "org_b": str(ORG_B),
                "property_b": str(property_b),
            },
        )
        await session.execute(
            sa.text(
                "INSERT INTO charge_entries "
                "(id, organization_id, recurring_charge_id, period, amount, created_by) "
                "VALUES "
                "(:id_a, :org_a, :charge_a, :period, 15000, :user_a), "
                "(:id_b, :org_b, :charge_b, :period, 15000, :user_b)"
            ),
            {
                "id_a": str(charge_entry_a),
                "org_a": str(ORG_A),
                "charge_a": str(recurring_charge_a),
                "id_b": str(charge_entry_b),
                "org_b": str(ORG_B),
                "charge_b": str(recurring_charge_b),
                "period": _PERIOD,
                "user_a": str(user_a),
                "user_b": str(user_b),
            },
        )
        await session.execute(
            sa.text(
                "INSERT INTO settlements "
                "(id, organization_id, landlord_id, period, commission_pct_used, generated_by) "
                "VALUES "
                "(:id_a, :org_a, :landlord_a, :period, 10, :user_a), "
                "(:id_b, :org_b, :landlord_b, :period, 10, :user_b)"
            ),
            {
                "id_a": str(settlement_a),
                "org_a": str(ORG_A),
                "landlord_a": str(landlord_a),
                "id_b": str(settlement_b),
                "org_b": str(ORG_B),
                "landlord_b": str(landlord_b),
                "period": _PERIOD,
                "user_a": str(user_a),
                "user_b": str(user_b),
            },
        )
        await session.execute(
            sa.text(
                "INSERT INTO settlement_line_items "
                "(id, organization_id, settlement_id, line_type, original_amount, "
                "original_currency, amount_ars) "
                "VALUES "
                "(:id_a, :org_a, :settlement_a, 'rent_collected', 100000, 'ARS', 100000), "
                "(:id_b, :org_b, :settlement_b, 'rent_collected', 100000, 'ARS', 100000)"
            ),
            {
                "id_a": str(line_item_a),
                "org_a": str(ORG_A),
                "settlement_a": str(settlement_a),
                "id_b": str(line_item_b),
                "org_b": str(ORG_B),
                "settlement_b": str(settlement_b),
            },
        )
    yield
    async with session_factory() as session, session.begin():
        # issue #42: teardown cruza dos organizaciones -- bypass RLS.
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        await session.execute(
            sa.text("DELETE FROM settlement_line_items WHERE organization_id IN (:org_a, :org_b)"),
            {"org_a": str(ORG_A), "org_b": str(ORG_B)},
        )
        await session.execute(
            sa.text("DELETE FROM settlements WHERE organization_id IN (:org_a, :org_b)"),
            {"org_a": str(ORG_A), "org_b": str(ORG_B)},
        )
        await session.execute(
            sa.text("DELETE FROM charge_entries WHERE organization_id IN (:org_a, :org_b)"),
            {"org_a": str(ORG_A), "org_b": str(ORG_B)},
        )
        await session.execute(
            sa.text("DELETE FROM recurring_charges WHERE organization_id IN (:org_a, :org_b)"),
            {"org_a": str(ORG_A), "org_b": str(ORG_B)},
        )
        await session.execute(
            sa.text("DELETE FROM properties WHERE organization_id IN (:org_a, :org_b)"),
            {"org_a": str(ORG_A), "org_b": str(ORG_B)},
        )
        await session.execute(
            sa.text("DELETE FROM landlords WHERE organization_id IN (:org_a, :org_b)"),
            {"org_a": str(ORG_A), "org_b": str(ORG_B)},
        )
        await session.execute(
            sa.text("DELETE FROM users WHERE id IN (:user_a, :user_b)"),
            {"user_a": str(user_a), "user_b": str(user_b)},
        )
        await session.execute(
            sa.text("DELETE FROM organizations WHERE id IN (:org_a, :org_b)"),
            {"org_a": str(ORG_A), "org_b": str(ORG_B)},
        )


@pytest.mark.parametrize("table", _TABLES)
async def test_ca_27_tenant_a_solo_ve_sus_propias_filas(
    table: str, two_orgs_with_settlement_rows_each
):
    """RN-D01: con `app.current_tenant_id = ORG_A`, `adminprop_app` solo ve
    la fila de la organizacion A, en las cuatro tablas de la Capa 6."""
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
        await set_tenant_context(session, ORG_A)
        result = await session.execute(sa.text(f"SELECT organization_id FROM {table}"))
        seen = {row[0] for row in result}

    assert seen == {ORG_A}


@pytest.mark.parametrize("table", _TABLES)
async def test_ca_27_tenant_b_solo_ve_sus_propias_filas(
    table: str, two_orgs_with_settlement_rows_each
):
    """Simetrico al test de A: `app.current_tenant_id = ORG_B`."""
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
        await set_tenant_context(session, ORG_B)
        result = await session.execute(sa.text(f"SELECT organization_id FROM {table}"))
        seen = {row[0] for row in result}

    assert seen == {ORG_B}


@pytest.mark.parametrize("table", _TABLES)
async def test_ca_27_sin_contexto_seteado_no_ve_ninguna_fila(
    table: str, two_orgs_with_settlement_rows_each
):
    """Fail-closed -- missing_ok=true + NULLIF cierran el acceso en vez de
    tirar un error 500 cuando nadie seteo el contexto de tenant."""
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
        result = await session.execute(sa.text(f"SELECT organization_id FROM {table}"))
        rows = list(result)

    assert rows == []


@pytest.mark.parametrize("table", _TABLES)
async def test_ca_27_contexto_limpiado_a_none_no_ve_ninguna_fila_ni_revienta(
    table: str, two_orgs_with_settlement_rows_each
):
    """`set_tenant_context(session, None)` (patron de rutas /superadmin/*)
    no revienta el cast a uuid -- devuelve 0 filas (fix NULLIF, issue #3)."""
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
        await set_tenant_context(session, None)
        result = await session.execute(sa.text(f"SELECT organization_id FROM {table}"))
        rows = list(result)

    assert rows == []


@pytest.mark.parametrize("table", _TABLES)
async def test_ca_27_adminprop_superadmin_bypassa_rls_y_ve_ambos_tenants(
    table: str, two_orgs_with_settlement_rows_each
):
    """Decision #42: `adminprop_superadmin` (BYPASSRLS) ve las filas de
    ambas organizaciones, sin necesidad de `set_tenant_context`."""
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        result = await session.execute(
            sa.text(f"SELECT organization_id FROM {table} WHERE organization_id IN (:a, :b)"),
            {"a": str(ORG_A), "b": str(ORG_B)},
        )
        seen = {row[0] for row in result}

    assert seen == {ORG_A, ORG_B}
