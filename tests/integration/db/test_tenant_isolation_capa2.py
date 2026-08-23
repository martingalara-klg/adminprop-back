"""Issue #14 — Aislamiento cross-tenant (RN-D01) sobre
`properties`/`property_service_accounts`.

Mismo patron que `tests/integration/db/test_tenant_isolation_capa1.py`
(issue #12, tablas `landlords`/`renters`): ejerce el aislamiento
end-to-end con `set_tenant_context`, no solo inspecciona el catalogo
(eso lo cubre `test_capa2_propiedades.py`).

SDD: core/sdd_02_domain_model.md §3 RN-D01
     + docs/skills/tenant-isolation.md
Implements: CA-14-02 (RLS aisla cross-tenant en ambas tablas)
"""

import uuid
from collections.abc import AsyncGenerator

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_session_factory, set_tenant_context

pytestmark = pytest.mark.asyncio

ORG_A = uuid.uuid4()
ORG_B = uuid.uuid4()


@pytest.fixture
async def two_orgs_with_one_property_and_service_account_each() -> AsyncGenerator[None]:
    """Crea dos organizaciones, un landlord + una property + una cuenta de
    servicio por cada una (conexion por default: `adminprop`, superuser con
    BYPASSRLS — set up ajeno al aislamiento que se quiere probar). Limpieza
    al final."""
    session_factory = get_session_factory()
    landlord_a = uuid.uuid4()
    landlord_b = uuid.uuid4()
    property_a = uuid.uuid4()
    property_b = uuid.uuid4()

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
                "INSERT INTO property_service_accounts "
                "(organization_id, property_id, service_type, account_number) "
                "VALUES (:org_a, :property_a, 'luz', 'CTA-A-001'), "
                "(:org_b, :property_b, 'luz', 'CTA-B-001')"
            ),
            {
                "org_a": str(ORG_A),
                "property_a": str(property_a),
                "org_b": str(ORG_B),
                "property_b": str(property_b),
            },
        )
    yield
    async with session_factory() as session, session.begin():
        # issue #42: teardown cruza dos organizaciones -- bypass RLS.
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        await session.execute(
            sa.text(
                "DELETE FROM property_service_accounts WHERE organization_id IN (:org_a, :org_b)"
            ),
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
            sa.text("DELETE FROM organizations WHERE id IN (:org_a, :org_b)"),
            {"org_a": str(ORG_A), "org_b": str(ORG_B)},
        )


@pytest.mark.parametrize("table", ["properties", "property_service_accounts"])
async def test_ca_14_02_tenant_a_solo_ve_sus_propias_filas(
    table: str, two_orgs_with_one_property_and_service_account_each
):
    """RN-D01: con `app.current_tenant_id = ORG_A`, `adminprop_app` solo ve
    la fila de la organizacion A, tanto en `properties` como en
    `property_service_accounts`."""
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
        await set_tenant_context(session, ORG_A)
        result = await session.execute(sa.text(f"SELECT organization_id FROM {table}"))
        seen = {row[0] for row in result}

    assert seen == {ORG_A}


@pytest.mark.parametrize("table", ["properties", "property_service_accounts"])
async def test_ca_14_02_tenant_b_solo_ve_sus_propias_filas(
    table: str, two_orgs_with_one_property_and_service_account_each
):
    """Simetrico al test de A: `app.current_tenant_id = ORG_B`."""
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
        await set_tenant_context(session, ORG_B)
        result = await session.execute(sa.text(f"SELECT organization_id FROM {table}"))
        seen = {row[0] for row in result}

    assert seen == {ORG_B}


@pytest.mark.parametrize("table", ["properties", "property_service_accounts"])
async def test_ca_14_02_sin_contexto_seteado_no_ve_ninguna_fila(
    table: str, two_orgs_with_one_property_and_service_account_each
):
    """Fail-closed — missing_ok=true + NULLIF cierran el acceso en vez de
    tirar un error 500 cuando nadie seteo el contexto de tenant."""
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
        result = await session.execute(sa.text(f"SELECT organization_id FROM {table}"))
        rows = list(result)

    assert rows == []


@pytest.mark.parametrize("table", ["properties", "property_service_accounts"])
async def test_ca_14_02_contexto_limpiado_a_none_no_ve_ninguna_fila_ni_revienta(
    table: str, two_orgs_with_one_property_and_service_account_each
):
    """`set_tenant_context(session, None)` (patron de rutas /superadmin/*)
    no revienta el cast a uuid — devuelve 0 filas (fix NULLIF, issue #3)."""
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
        await set_tenant_context(session, None)
        result = await session.execute(sa.text(f"SELECT organization_id FROM {table}"))
        rows = list(result)

    assert rows == []


@pytest.mark.parametrize("table", ["properties", "property_service_accounts"])
async def test_ca_14_02_adminprop_superadmin_bypassa_rls_y_ve_ambos_tenants(
    table: str, two_orgs_with_one_property_and_service_account_each
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
