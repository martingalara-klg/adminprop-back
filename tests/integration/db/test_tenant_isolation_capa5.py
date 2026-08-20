"""Issue #25 — Aislamiento cross-tenant (RN-D01) sobre
`work_orders`/`work_order_quotes`/`attachments`.

Mismo patron que `tests/integration/db/test_tenant_isolation_capa4.py`
(issue #20): ejerce el aislamiento end-to-end con `set_tenant_context`,
no solo inspecciona el catalogo (eso lo cubre `test_capa5_mantenimiento.py`).

SDD: core/sdd_02_domain_model.md §3 RN-D01
     + docs/skills/tenant-isolation.md
Implements: CA-25 (RLS aisla cross-tenant en las tres tablas de la Capa 5)
"""

import uuid
from collections.abc import AsyncGenerator

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_session_factory, set_tenant_context

pytestmark = pytest.mark.asyncio

ORG_A = uuid.uuid4()
ORG_B = uuid.uuid4()

_TABLES = ("work_orders", "work_order_quotes", "attachments")


@pytest.fixture
async def two_orgs_with_maintenance_rows_each() -> AsyncGenerator[None]:
    """Crea dos organizaciones, cada una con landlord + property + un
    usuario + un work_order + una work_order_quote + un attachment
    (conexion por default: `adminprop`, superuser con BYPASSRLS -- setup
    ajeno al aislamiento que se quiere probar). Limpieza al final."""
    session_factory = get_session_factory()
    landlord_a, landlord_b = uuid.uuid4(), uuid.uuid4()
    property_a, property_b = uuid.uuid4(), uuid.uuid4()
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    work_order_a, work_order_b = uuid.uuid4(), uuid.uuid4()
    quote_a, quote_b = uuid.uuid4(), uuid.uuid4()
    attachment_a, attachment_b = uuid.uuid4(), uuid.uuid4()

    async with session_factory() as session, session.begin():
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
                "INSERT INTO work_orders "
                "(id, organization_id, property_id, title, payer, created_by) "
                "VALUES "
                "(:id_a, :org_a, :property_a, 'Pedido A', 'landlord', :user_a), "
                "(:id_b, :org_b, :property_b, 'Pedido B', 'landlord', :user_b)"
            ),
            {
                "id_a": str(work_order_a),
                "org_a": str(ORG_A),
                "property_a": str(property_a),
                "user_a": str(user_a),
                "id_b": str(work_order_b),
                "org_b": str(ORG_B),
                "property_b": str(property_b),
                "user_b": str(user_b),
            },
        )
        await session.execute(
            sa.text(
                "INSERT INTO work_order_quotes "
                "(id, organization_id, work_order_id, amount, submitted_by) "
                "VALUES "
                "(:id_a, :org_a, :work_order_a, 50000, :user_a), "
                "(:id_b, :org_b, :work_order_b, 50000, :user_b)"
            ),
            {
                "id_a": str(quote_a),
                "org_a": str(ORG_A),
                "work_order_a": str(work_order_a),
                "user_a": str(user_a),
                "id_b": str(quote_b),
                "org_b": str(ORG_B),
                "work_order_b": str(work_order_b),
                "user_b": str(user_b),
            },
        )
        await session.execute(
            sa.text(
                "INSERT INTO attachments "
                "(id, organization_id, entity_type, entity_id, file_path, file_name, "
                "mime_type, size_bytes, uploaded_by) "
                "VALUES "
                "(:id_a, :org_a, 'work_order', :work_order_a, '/data/a/foo.jpg', 'foo.jpg', "
                "'image/jpeg', 1024, :user_a), "
                "(:id_b, :org_b, 'work_order', :work_order_b, '/data/b/foo.jpg', 'foo.jpg', "
                "'image/jpeg', 1024, :user_b)"
            ),
            {
                "id_a": str(attachment_a),
                "org_a": str(ORG_A),
                "work_order_a": str(work_order_a),
                "user_a": str(user_a),
                "id_b": str(attachment_b),
                "org_b": str(ORG_B),
                "work_order_b": str(work_order_b),
                "user_b": str(user_b),
            },
        )
    yield
    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text("DELETE FROM attachments WHERE organization_id IN (:org_a, :org_b)"),
            {"org_a": str(ORG_A), "org_b": str(ORG_B)},
        )
        await session.execute(
            sa.text("DELETE FROM work_order_quotes WHERE organization_id IN (:org_a, :org_b)"),
            {"org_a": str(ORG_A), "org_b": str(ORG_B)},
        )
        await session.execute(
            sa.text(
                "UPDATE work_orders SET approved_quote_id = NULL "
                "WHERE organization_id IN (:org_a, :org_b)"
            ),
            {"org_a": str(ORG_A), "org_b": str(ORG_B)},
        )
        await session.execute(
            sa.text("DELETE FROM work_orders WHERE organization_id IN (:org_a, :org_b)"),
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
async def test_ca_25_tenant_a_solo_ve_sus_propias_filas(
    table: str, two_orgs_with_maintenance_rows_each
):
    """RN-D01: con `app.current_tenant_id = ORG_A`, `adminprop_app` solo ve
    la fila de la organizacion A, en las tres tablas de la Capa 5."""
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
        await set_tenant_context(session, ORG_A)
        result = await session.execute(sa.text(f"SELECT organization_id FROM {table}"))
        seen = {row[0] for row in result}

    assert seen == {ORG_A}


@pytest.mark.parametrize("table", _TABLES)
async def test_ca_25_tenant_b_solo_ve_sus_propias_filas(
    table: str, two_orgs_with_maintenance_rows_each
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
async def test_ca_25_sin_contexto_seteado_no_ve_ninguna_fila(
    table: str, two_orgs_with_maintenance_rows_each
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
async def test_ca_25_contexto_limpiado_a_none_no_ve_ninguna_fila_ni_revienta(
    table: str, two_orgs_with_maintenance_rows_each
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
async def test_ca_25_adminprop_superadmin_bypassa_rls_y_ve_ambos_tenants(
    table: str, two_orgs_with_maintenance_rows_each
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
