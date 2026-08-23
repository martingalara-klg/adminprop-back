"""Issue #10 — Migracion audit_logs: schema, RLS y append-only a nivel de
permisos de PostgreSQL.

Requiere Postgres real con `alembic upgrade head` ya corrido -- mismo
patron que `tests/integration/db/test_capa0_fundacion.py` y
`tests/integration/db/test_tenant_isolation.py`.

SDD: infrastructure/spec_data_model.md §Capa 7 "audit_logs"
     + core/sdd_02_domain_model.md §2.17
Implements: RN-D03 (append-only e inmutable)
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_engine, get_session_factory, set_tenant_context

pytestmark = pytest.mark.asyncio

_COLUMNS = {
    "id",
    "organization_id",
    "user_id",
    "action",
    "entity_type",
    "entity_id",
    "before_state",
    "after_state",
    "request_id",
    "created_at",
}


async def test_ca_10_01_audit_logs_columnas_identicas_al_spec():
    """spec_data_model.md §Capa 7 "audit_logs": columnas exactas."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_logs'"
            )
        )
        columns = {row[0] for row in result}
    assert columns == _COLUMNS


async def test_ca_10_01_audit_logs_tiene_rls_habilitado_y_forzado():
    """Mismo patron RLS+FORCE que Capa 0 (docs/skills/database-migration.md)."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = 'audit_logs'"
            )
        )
        row = result.one()
    assert row.relrowsecurity is True
    assert row.relforcerowsecurity is True


async def test_ca_10_01_politica_tenant_isolation_usa_nullif_en_el_cast():
    """Mismo fix NULLIF que el resto de las tablas tenant-scoped (issue #3)."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT pg_get_expr(polqual, polrelid) FROM pg_policy "
                "WHERE polrelid = to_regclass('audit_logs')"
            )
        )
        qual = result.scalar_one()
    assert "NULLIF" in qual
    assert "app.current_tenant_id" in qual


async def test_ca_10_01_indices_del_spec_existen():
    """spec_data_model.md §"Indices PostgreSQL Recomendados":
    (organization_id, created_at) y (entity_type, entity_id)."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE tablename = 'audit_logs'")
        )
        defs = [row[0] for row in result]
    assert any("organization_id" in d and "created_at" in d for d in defs)
    assert any("entity_type" in d and "entity_id" in d for d in defs)


class TestAppendOnlyEnforcedAtPermissionLevel:
    """RN-D03: append-only enforced a nivel de permisos de PostgreSQL.

    El pool de tests puede conectar como superuser (issue #42 pendiente)
    -- se hace `SET LOCAL ROLE adminprop_app` explicito dentro de la
    transaccion para probar el REVOKE de verdad (mismo patron que
    `tests/integration/db/test_tenant_isolation.py`), no el rol con el
    que arranca la conexion del pool.
    """

    async def test_ca_10_01_adminprop_app_puede_insertar_y_leer(self):
        org_id = uuid.uuid4()
        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            # issue #42: seed cruza el bootstrap de la organizacion (sin
            # tenant context todavia) -- bypass RLS explicito, no se testea
            # aislamiento en este bloque.
            await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
            await session.execute(
                sa.text("INSERT INTO organizations (id, slug, name) VALUES (:id, :slug, :name)"),
                {"id": str(org_id), "slug": f"org-{org_id.hex[:8]}", "name": "Org append-only"},
            )
            await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
            await set_tenant_context(session, org_id)
            await session.execute(
                sa.text(
                    "INSERT INTO audit_logs (organization_id, action, entity_type) "
                    "VALUES (:org_id, 'org.created', 'organization')"
                ),
                {"org_id": str(org_id)},
            )
            result = await session.execute(
                sa.text("SELECT action FROM audit_logs WHERE organization_id = :org_id"),
                {"org_id": str(org_id)},
            )
            actions = [row[0] for row in result]
        assert actions == ["org.created"]

    async def test_ca_10_01_adminprop_app_no_puede_hacer_update(self):
        org_id = uuid.uuid4()
        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            # issue #42: seed plano, sin tenant context -- bypass RLS.
            await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
            await session.execute(
                sa.text("INSERT INTO organizations (id, slug, name) VALUES (:id, :slug, :name)"),
                {"id": str(org_id), "slug": f"org-{org_id.hex[:8]}", "name": "Org append-only"},
            )
            await session.execute(
                sa.text(
                    "INSERT INTO audit_logs (organization_id, action, entity_type) "
                    "VALUES (:org_id, 'org.created', 'organization')"
                ),
                {"org_id": str(org_id)},
            )

        async with session_factory() as session, session.begin():
            await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
            await set_tenant_context(session, org_id)
            with pytest.raises(sa.exc.DBAPIError, match="permission denied"):
                await session.execute(
                    sa.text(
                        "UPDATE audit_logs SET action = 'tampered' WHERE organization_id = :org_id"
                    ),
                    {"org_id": str(org_id)},
                )

    async def test_ca_10_01_adminprop_app_no_puede_hacer_delete(self):
        org_id = uuid.uuid4()
        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            # issue #42: seed plano, sin tenant context -- bypass RLS.
            await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
            await session.execute(
                sa.text("INSERT INTO organizations (id, slug, name) VALUES (:id, :slug, :name)"),
                {"id": str(org_id), "slug": f"org-{org_id.hex[:8]}", "name": "Org append-only"},
            )
            await session.execute(
                sa.text(
                    "INSERT INTO audit_logs (organization_id, action, entity_type) "
                    "VALUES (:org_id, 'org.created', 'organization')"
                ),
                {"org_id": str(org_id)},
            )

        async with session_factory() as session, session.begin():
            await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
            await set_tenant_context(session, org_id)
            with pytest.raises(sa.exc.DBAPIError, match="permission denied"):
                await session.execute(
                    sa.text("DELETE FROM audit_logs WHERE organization_id = :org_id"),
                    {"org_id": str(org_id)},
                )
