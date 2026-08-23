"""Issue #5 — Aislamiento cross-tenant (RN-D01) sobre una tabla real de la
Capa 0 (`roles`) creada por la migracion `20260812_212704`.

A diferencia de `tests/integration/db/test_tenant_isolation.py` (issue #3,
que usa una tabla "probe" descartable porque todavia no existian tablas de
negocio), esta suite ejerce el aislamiento sobre `roles`, que ya tiene el
patron RLS + FORCE + `NULLIF(...)::uuid` definido en la migracion real del
issue #5.

SDD: core/sdd_02_domain_model.md §3 RN-D01
     + docs/skills/tenant-isolation.md
Implements: CA-5-03 (test de aislamiento cross-tenant) — verificado
            end-to-end con `set_tenant_context`, no solo inspeccionando
            el catalogo (eso lo cubre CA-5-02 en test_capa0_fundacion.py).
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
async def two_orgs_with_one_role_each() -> AsyncGenerator[None]:
    """Crea dos organizaciones y un rol por cada una (conexion por default:
    `adminprop`, superuser con BYPASSRLS — set up ajeno al aislamiento que
    se quiere probar). Limpieza al final, en el mismo orden de FKs.
    """
    session_factory = get_session_factory()
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
                "INSERT INTO roles (organization_id, name, permissions) "
                "VALUES "
                "(:org_a, 'owner', '[\"contract:manage\"]'::jsonb), "
                "(:org_b, 'owner', '[\"contract:manage\"]'::jsonb)"
            ),
            {"org_a": str(ORG_A), "org_b": str(ORG_B)},
        )
    yield
    async with session_factory() as session, session.begin():
        # issue #42: teardown cruza dos organizaciones -- bypass RLS.
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        await session.execute(
            sa.text("DELETE FROM roles WHERE organization_id IN (:org_a, :org_b)"),
            {"org_a": str(ORG_A), "org_b": str(ORG_B)},
        )
        await session.execute(
            sa.text("DELETE FROM organizations WHERE id IN (:org_a, :org_b)"),
            {"org_a": str(ORG_A), "org_b": str(ORG_B)},
        )


async def test_ca_5_03_tenant_a_solo_ve_sus_propios_roles(two_orgs_with_one_role_each):
    """CA-5-03 / RN-D01: con `app.current_tenant_id = ORG_A`, `adminprop_app`
    solo ve el rol de la organizacion A.
    """
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
        await set_tenant_context(session, ORG_A)
        result = await session.execute(sa.text("SELECT organization_id FROM roles"))
        seen = {row[0] for row in result}

    assert seen == {ORG_A}


async def test_ca_5_03_tenant_b_solo_ve_sus_propios_roles(two_orgs_with_one_role_each):
    """CA-5-03 / RN-D01: con `app.current_tenant_id = ORG_B`, `adminprop_app`
    solo ve el rol de la organizacion B (simetrico al test de A).
    """
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
        await set_tenant_context(session, ORG_B)
        result = await session.execute(sa.text("SELECT organization_id FROM roles"))
        seen = {row[0] for row in result}

    assert seen == {ORG_B}


async def test_ca_5_03_sin_contexto_seteado_no_ve_ningun_rol(two_orgs_with_one_role_each):
    """CA-5-03 / RN-D01: `adminprop_app` sin `set_tenant_context` previo no ve
    filas de ninguna organizacion (fail-closed — missing_ok=true + NULLIF
    cierran el acceso en vez de tirar un error 500).
    """
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
        result = await session.execute(sa.text("SELECT organization_id FROM roles"))
        rows = list(result)

    assert rows == []


async def test_ca_5_03_contexto_limpiado_a_none_no_ve_ningun_rol_ni_revienta(
    two_orgs_with_one_role_each,
):
    """CA-5-03 / RN-D01: `set_tenant_context(session, None)` (limpieza explicita,
    patron de rutas `/superadmin/*`) no revienta el cast a uuid — devuelve 0
    filas en vez de un error 500 (fix NULLIF, issue #3).
    """
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
        await set_tenant_context(session, None)
        result = await session.execute(sa.text("SELECT organization_id FROM roles"))
        rows = list(result)

    assert rows == []


async def test_ca_5_02_adminprop_superadmin_bypassa_rls_y_ve_ambos_tenants(
    two_orgs_with_one_role_each,
):
    """Decision #42: `adminprop_superadmin` (BYPASSRLS) ve los roles de
    ambas organizaciones, sin necesidad de `set_tenant_context`.
    """
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        result = await session.execute(
            sa.text("SELECT organization_id FROM roles WHERE organization_id IN (:a, :b)"),
            {"a": str(ORG_A), "b": str(ORG_B)},
        )
        seen = {row[0] for row in result}

    assert seen == {ORG_A, ORG_B}
