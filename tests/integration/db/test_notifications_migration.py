"""Issue #11 — Migracion notifications: schema, RLS e indices.

Requiere Postgres real con `alembic upgrade head` ya corrido -- mismo
patron que `tests/integration/db/test_audit_logs.py`.

SDD: infrastructure/spec_data_model.md §Capa 7 "notifications"
Implements: CA-NT-02 (soporte de tabla para la invariante transaccional,
            probada de punta a punta en tests/integration/notifications),
            RN-D01 (RLS + FORCE)
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_engine

pytestmark = pytest.mark.asyncio

_COLUMNS = {
    "id",
    "organization_id",
    "user_id",
    "event_type",
    "payload",
    "read_at",
    "email_sent_at",
    "created_at",
}

_EVENT_TYPES = {
    "adjustment_pending",
    "contract_expiring",
    "quote_submitted",
    "work_order_created",
    "work_order_closed",
}


async def test_ca_nt_notifications_columnas_identicas_al_spec():
    """spec_data_model.md §Capa 7 "notifications": columnas exactas."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'notifications'"
            )
        )
        columns = {row[0] for row in result}
    assert columns == _COLUMNS


async def test_ca_nt_notifications_tiene_rls_habilitado_y_forzado():
    """RN-D01: mismo patron RLS+FORCE que Capa 0 y audit_logs."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = 'notifications'"
            )
        )
        row = result.one()
    assert row.relrowsecurity is True
    assert row.relforcerowsecurity is True


async def test_ca_nt_politica_tenant_isolation_usa_nullif_en_el_cast():
    """Mismo fix NULLIF que el resto de las tablas tenant-scoped (issue #3)."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT pg_get_expr(polqual, polrelid) FROM pg_policy "
                "WHERE polrelid = to_regclass('notifications')"
            )
        )
        qual = result.scalar_one()
    assert "NULLIF" in qual
    assert "app.current_tenant_id" in qual


async def test_ca_nt_indice_parcial_del_spec_existe():
    """spec_data_model.md §"Indices PostgreSQL Recomendados":
    (user_id) WHERE read_at IS NULL."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE tablename = 'notifications'")
        )
        defs = [row[0] for row in result]
    assert any(
        "user_id" in d and "read_at IS NULL" in d for d in defs
    ), f"esperaba un indice parcial (user_id) WHERE read_at IS NULL, defs={defs}"


async def test_ca_nt_event_type_check_tiene_los_5_valores_del_mvp():
    """spec_notificaciones.md "Eventos del MVP": exactamente estos 5."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = to_regclass('notifications') AND contype = 'c'"
            )
        )
        definitions = " ".join(row[0] for row in result)
    for event_type in _EVENT_TYPES:
        assert event_type in definitions
