"""Issue #3 — set_tenant_context + RLS: aislamiento de sesion (RN-D01).

No hay tablas de negocio todavia (llegan con el issue #5): este modulo es
la infraestructura de migraciones + el hook de sesion. Para validar que
`set_tenant_context` efectivamente hace cumplir el aislamiento multi-
tenant se usa una tabla "probe" descartable, creada y eliminada dentro
del propio test, con exactamente el patron RLS de
`docs/skills/database-migration.md` (incluido el fix `NULLIF` descubierto
en este mismo issue — ver `docs/skills/tenant-isolation.md` antipatrones).

SDD: core/sdd_02_domain_model.md §3 RN-D01
     + docs/skills/tenant-isolation.md
"""

import uuid
from collections.abc import AsyncGenerator

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from adminprop.config import get_settings
from adminprop.db.session import (
    get_session_factory,
    set_tenant_context,
    tenant_scoped_session,
    to_async_dsn,
)

pytestmark = pytest.mark.asyncio

_PROBE_TABLE = "scratch_issue3_tenant_probe"
ORG_A = uuid.uuid4()
ORG_B = uuid.uuid4()


@pytest.fixture
async def _superuser_session_factory() -> AsyncGenerator[async_sessionmaker]:
    """Issue #42: `get_session_factory()` ahora conecta como `adminprop_app`,
    que solo tiene `USAGE` (no `CREATE`) sobre el schema `public` -- ni
    `adminprop_superadmin` (BYPASSRLS, pero tampoco tiene `CREATE` en el
    schema) puede crear la tabla "probe" descartable de este modulo. Esta
    fixture crea un engine efimero con el superusuario de Postgres
    (`Settings.migrations_database_url`, el mismo rol que usa Alembic) SOLO
    para el DDL de `probe_table` de abajo -- las aserciones de los tests
    siguen usando `get_session_factory()` (adminprop_app real) sin cambios.
    """
    engine = create_async_engine(to_async_dsn(get_settings().migrations_database_url))
    try:
        yield async_sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def probe_table(_superuser_session_factory: async_sessionmaker):
    """Tabla RLS descartable — no forma parte del modelo de datos del SDD."""
    session_factory = _superuser_session_factory
    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text(
                f"""
                    CREATE TABLE {_PROBE_TABLE} (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        organization_id UUID NOT NULL,
                        label TEXT NOT NULL
                    )
                    """
            )
        )
        await session.execute(sa.text(f"ALTER TABLE {_PROBE_TABLE} ENABLE ROW LEVEL SECURITY"))
        await session.execute(
            sa.text(
                f"""
                    CREATE POLICY {_PROBE_TABLE}_iso ON {_PROBE_TABLE}
                    USING (
                        organization_id
                        = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                    )
                    WITH CHECK (
                        organization_id
                        = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                    )
                    """
            )
        )
        await session.execute(sa.text(f"ALTER TABLE {_PROBE_TABLE} FORCE ROW LEVEL SECURITY"))
        await session.execute(sa.text(f"GRANT SELECT, INSERT ON {_PROBE_TABLE} TO adminprop_app"))
        await session.execute(sa.text(f"GRANT SELECT ON {_PROBE_TABLE} TO adminprop_superadmin"))
        await session.execute(
            sa.text(
                f"INSERT INTO {_PROBE_TABLE} (organization_id, label) "
                "VALUES (:org_a, 'org-a-row'), (:org_b, 'org-b-row')"
            ),
            {"org_a": str(ORG_A), "org_b": str(ORG_B)},
        )
    yield
    async with session_factory() as session, session.begin():
        await session.execute(sa.text(f"DROP TABLE IF EXISTS {_PROBE_TABLE}"))


async def test_tenant_isolation_set_tenant_context_scopes_rows_to_current_tenant(probe_table):
    """CA #3-03: set_tenant_context + RLS devuelven solo las filas del tenant activo."""
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
        await set_tenant_context(session, ORG_A)
        result = await session.execute(sa.text(f"SELECT label FROM {_PROBE_TABLE}"))
        rows = {row[0] for row in result}

    assert rows == {"org-a-row"}


async def test_tenant_isolation_set_tenant_context_is_scoped_to_the_transaction(probe_table):
    """CA #3-03: SET LOCAL no se filtra a la siguiente transaccion de la misma
    sesion (PgBouncer es transaction-scoped: el contexto no debe sobrevivir).
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            await set_tenant_context(session, ORG_A)
            result = await session.execute(
                sa.text("SELECT current_setting('app.current_tenant_id', true)")
            )
            assert result.scalar_one() == str(ORG_A)

        async with session.begin():
            result = await session.execute(
                sa.text("SELECT current_setting('app.current_tenant_id', true)")
            )
            value = result.scalar_one()

    assert value in (None, "")


async def test_tenant_isolation_missing_context_returns_zero_rows_for_adminprop_app(probe_table):
    """CA #3-03: adminprop_app sin contexto seteado no ve ninguna fila (fail-closed,
    RN-D01) — `missing_ok=true` + `NULLIF` cierran el acceso en vez de tirar error.
    """
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
        result = await session.execute(sa.text(f"SELECT label FROM {_PROBE_TABLE}"))
        rows = list(result)

    assert rows == []


async def test_tenant_isolation_cleared_context_returns_zero_rows_not_an_error(probe_table):
    """CA #3-03: set_tenant_context(session, None) (limpieza explicita, ej.
    rutas /superadmin/*) no revienta el cast a uuid — devuelve 0 filas.
    """
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
        await set_tenant_context(session, None)
        result = await session.execute(sa.text(f"SELECT label FROM {_PROBE_TABLE}"))
        rows = list(result)

    assert rows == []


async def test_tenant_isolation_adminprop_superadmin_bypasses_rls_across_tenants(probe_table):
    """CA #3-03 / Decision #42: adminprop_superadmin (BYPASSRLS) ve todos los tenants."""
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        result = await session.execute(sa.text(f"SELECT label FROM {_PROBE_TABLE} ORDER BY label"))
        rows = [row[0] for row in result]

    assert rows == ["org-a-row", "org-b-row"]


async def test_tenant_isolation_tenant_scoped_session_helper_sets_context_for_workers(
    probe_table,
):
    """CA #3-03: `tenant_scoped_session` (helper "por tarea" para Celery) fija
    el tenant sin depender de un middleware HTTP — docs/skills/tenant-isolation.md.
    """
    async with tenant_scoped_session(ORG_B) as session:
        await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
        result = await session.execute(sa.text(f"SELECT label FROM {_PROBE_TABLE}"))
        rows = {row[0] for row in result}

    assert rows == {"org-b-row"}
