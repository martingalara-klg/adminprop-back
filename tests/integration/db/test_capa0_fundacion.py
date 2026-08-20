"""Issue #5 — Migracion Capa 0: organizations, users, roles, members,
invitations + RLS.

Requiere Postgres real (levantado por `docker/docker-compose.yml` local o
por el servicio `postgres` de `.github/workflows/ci.yml`) con
`alembic upgrade head` ya corrido antes de esta suite — mismo patron que
`tests/integration/db/test_migrations.py` (issue #3).

SDD: infrastructure/spec_data_model.md §Capa 0 — Fundacion
     + §"Indices PostgreSQL Recomendados"
Implements: CA-5-01 (tablas identicas al spec), CA-5-02 (RLS + FORCE)
"""

import asyncio
import sys

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_engine

pytestmark = pytest.mark.asyncio

_CAPA0_TABLES = (
    "organizations",
    "users",
    "roles",
    "organization_members",
    "organization_invitations",
)
_TENANT_SCOPED_TABLES = ("roles", "organization_members", "organization_invitations")


async def test_ca_5_01_alembic_upgrade_head_deja_la_base_en_la_revision_actual():
    """CA-5-01: `alembic upgrade head` deja la base en la revision de esta migracion.

    Issue #25 agrego `20260819_150000_create_capa5_mantenimiento.py` encima
    de `20260819_140000_create_capa4_cobranzas.py` (issue #20) -- el head
    se actualiza a esa revision.
    """
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(sa.text("SELECT version_num FROM alembic_version"))
        version = result.scalar_one()
    assert version == "20260819_150000"


async def test_ca_5_01_las_cinco_tablas_de_capa0_existen():
    """CA-5-01: `organizations`, `users`, `roles`, `organization_members` y
    `organization_invitations` existen tras la migracion.
    """
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                "AND tablename = ANY(:tables)"
            ),
            {"tables": list(_CAPA0_TABLES)},
        )
        found = {row[0] for row in result}
    assert found == set(_CAPA0_TABLES)


async def test_ca_5_01_organizations_tiene_columnas_y_check_de_status_del_spec():
    """CA-5-01: `organizations` — columnas y CHECK de `status` identicos al spec
    (spec_data_model.md §Capa 0 "organizations"): pending_owner/active/disabled.
    """
    engine = get_engine()
    async with engine.connect() as conn:
        columns_result = await conn.execute(
            sa.text(
                "SELECT column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_name = 'organizations' ORDER BY column_name"
            )
        )
        columns = {row.column_name: row for row in columns_result}

        check_result = await conn.execute(
            sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'organizations'::regclass AND contype = 'c'"
            )
        )
        check_defs = [row[0] for row in check_result]

    expected_columns = {
        "id",
        "slug",
        "name",
        "status",
        "timezone",
        "settings",
        "created_at",
        "updated_at",
        "deleted_at",
    }
    assert set(columns) == expected_columns
    assert columns["slug"].is_nullable == "NO"
    assert columns["status"].column_default == "'pending_owner'::text"
    assert any("pending_owner" in d and "active" in d and "disabled" in d for d in check_defs)


async def test_ca_5_01_users_es_tabla_global_con_email_unique_y_password_hash():
    """CA-5-01: `users` — identidad global de login, `email` UNIQUE NOT NULL."""
    engine = get_engine()
    async with engine.connect() as conn:
        columns_result = await conn.execute(
            sa.text("SELECT column_name FROM information_schema.columns WHERE table_name = 'users'")
        )
        columns = {row[0] for row in columns_result}

        unique_result = await conn.execute(
            sa.text(
                "SELECT a.attname FROM pg_constraint c "
                "JOIN unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) ON true "
                "JOIN pg_attribute a ON a.attnum = k.attnum AND a.attrelid = c.conrelid "
                "WHERE c.conrelid = 'users'::regclass AND c.contype = 'u'"
            )
        )
        unique_columns = {row[0] for row in unique_result}

    expected_columns = {
        "id",
        "email",
        "password_hash",
        "full_name",
        "is_super_admin",
        "created_at",
        "updated_at",
        "deleted_at",
    }
    assert set(columns) == expected_columns
    assert "email" in unique_columns


async def test_ca_5_01_roles_tiene_unique_organization_id_name():
    """CA-5-01: `roles` — UNIQUE (organization_id, name) del spec."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'roles'::regclass AND contype = 'u'"
            )
        )
        defs = [row[0] for row in result]
    assert any("organization_id" in d and "name" in d for d in defs)


async def test_ca_5_01_organization_members_tiene_unique_organization_id_user_id():
    """CA-5-01: `organization_members` — UNIQUE (organization_id, user_id)
    (un rol por org, spec_data_model.md §Capa 0) + CHECK de status.
    """
    engine = get_engine()
    async with engine.connect() as conn:
        unique_result = await conn.execute(
            sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'organization_members'::regclass AND contype = 'u'"
            )
        )
        unique_defs = [row[0] for row in unique_result]

        check_result = await conn.execute(
            sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'organization_members'::regclass AND contype = 'c'"
            )
        )
        check_defs = [row[0] for row in check_result]

    assert any("organization_id" in d and "user_id" in d for d in unique_defs)
    assert any("active" in d and "inactive" in d for d in check_defs)


async def test_ca_5_01_organization_invitations_tiene_token_unique_y_check_status():
    """CA-5-01: `organization_invitations` — `token` UNIQUE + CHECK de status
    (pending/accepted/expired/revoked) del spec.
    """
    engine = get_engine()
    async with engine.connect() as conn:
        unique_result = await conn.execute(
            sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'organization_invitations'::regclass AND contype = 'u'"
            )
        )
        unique_defs = [row[0] for row in unique_result]

        check_result = await conn.execute(
            sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'organization_invitations'::regclass AND contype = 'c'"
            )
        )
        check_defs = [row[0] for row in check_result]

    assert any("token" in d for d in unique_defs)
    assert any(
        all(status in d for status in ("pending", "accepted", "expired", "revoked"))
        for d in check_defs
    )


@pytest.mark.parametrize("table", _TENANT_SCOPED_TABLES)
async def test_ca_5_02_rls_habilitado_y_forzado_en_tablas_tenant_scoped(table: str):
    """CA-5-02: `roles`, `organization_members` y `organization_invitations`
    tienen RLS habilitado + FORCE (docs/skills/database-migration.md).
    """
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


@pytest.mark.parametrize("table", _TENANT_SCOPED_TABLES)
async def test_ca_5_02_politica_tenant_isolation_usa_nullif_en_el_cast(table: str):
    """CA-5-02: la politica RLS usa el patron `NULLIF(current_setting(...), '')::uuid`
    (fix descubierto en el issue #3, docs/skills/tenant-isolation.md) — evita un 500
    cuando el contexto se limpia a string vacio en vez de NULL.
    """
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT pg_get_expr(polqual, polrelid) FROM pg_policy "
                "WHERE polrelid = to_regclass(:t)"
            ),
            {"t": table},
        )
        qual = result.scalar_one()
    assert "NULLIF" in qual
    assert "app.current_tenant_id" in qual


async def test_ca_5_02_organizations_y_users_no_tienen_rls():
    """CA-5-02: `organizations` (raiz) y `users` (identidad global) quedan
    excluidas de RLS por diseno (spec_data_model.md §Capa 0).
    """
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname IN ('organizations', 'users')"
            )
        )
        rows = {row.relname: row for row in result}
    assert rows["organizations"].relrowsecurity is False
    assert rows["users"].relrowsecurity is False


async def _run_alembic(*args: str) -> asyncio.subprocess.Process:
    """Corre `python -m alembic <args>` como subproceso async, en el mismo
    event loop que el resto de la suite — evita el problema de asyncpg
    "Future attached to a different loop" que aparece si se mezcla el
    engine cacheado (`get_engine`, `lru_cache`) con `asyncio.run()` (crea
    un event loop nuevo por invocacion; ver `tests/integration/db/conftest.py`).
    """
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await process.communicate()
    return process


async def _tables_present() -> set[str]:
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                "AND tablename = ANY(:tables)"
            ),
            {"tables": list(_CAPA0_TABLES)},
        )
        return {row[0] for row in result}


async def test_ca_5_01_migracion_revierte_y_reaplica_limpio():
    """CA-5-01: revertir la migracion de Capa 0 elimina sus 5 tablas sin
    error, y `alembic upgrade head` las vuelve a crear sin error — la
    migracion es reversible de punta a punta.

    Issue #10 agrego `20260814_190741_create_audit_logs.py` como nuevo
    head encima de Capa 0 -- `downgrade -1` ahora revierte esa migracion
    (no Capa 0). Se apunta al down_revision EXPLICITO de la migracion de
    Capa 0 (`20260812_114322`, ver `20260812_212704_create_capa0_fundacion.py`)
    para que este test siga probando especificamente la reversibilidad de
    Capa 0, sin importar cuantas migraciones se agreguen encima en el futuro.
    """
    downgrade = await _run_alembic("downgrade", "20260812_114322")
    assert downgrade.returncode == 0

    assert await _tables_present() == set()

    upgrade = await _run_alembic("upgrade", "head")
    assert upgrade.returncode == 0
    assert await _tables_present() == set(_CAPA0_TABLES)
