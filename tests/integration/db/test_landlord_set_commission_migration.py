"""Issue #51 — Migracion de datos: backfill de `landlord:set-commission`
en el rol `owner` de organizaciones existentes.

Requiere Postgres real con `alembic upgrade head` ya corrido -- mismo
patron que `tests/integration/db/test_migrations.py` (re-ejecuta la
misma sentencia SQL de la migracion contra una fila "vieja" sembrada a
mano, para probar el backfill sin necesitar recorrer la cadena de
revisiones de Alembic).

SDD: core/sdd_03_api_contracts.md v1.5 §"Catalogo de Permisos" +
     infrastructure/spec_data_model.md §"Estrategia de Seed Data".
Implements: CA-R50-02 (migracion de datos para orgs EXISTENTES).
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from adminprop.db.session import get_engine

pytestmark = pytest.mark.asyncio

_PERMISSION = "landlord:set-commission"

_UPGRADE_SQL = """
    UPDATE roles
    SET permissions = permissions || '["landlord:set-commission"]'::jsonb,
        updated_at = now()
    WHERE name = 'owner'
      AND NOT (permissions @> '["landlord:set-commission"]'::jsonb)
"""

_DOWNGRADE_SQL = """
    UPDATE roles
    SET permissions = (
            SELECT COALESCE(jsonb_agg(elem), '[]'::jsonb)
            FROM jsonb_array_elements(permissions) AS elem
            WHERE elem <> '"landlord:set-commission"'::jsonb
        ),
        updated_at = now()
    WHERE name = 'owner'
      AND permissions @> '["landlord:set-commission"]'::jsonb
"""


async def _seed_stale_owner_role(conn: AsyncConnection) -> tuple[uuid.UUID, uuid.UUID]:
    """Organizacion + rol `owner` SIN `landlord:set-commission` -- simula
    una fila creada ANTES de esta migracion (issue #13, catalogo viejo)."""
    org_id = uuid.uuid4()
    role_id = uuid.uuid4()
    await conn.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
    await conn.execute(
        sa.text(
            "INSERT INTO organizations (id, slug, name, status) "
            "VALUES (:id, :slug, :name, 'active')"
        ),
        {"id": str(org_id), "slug": f"org-{org_id.hex[:8]}", "name": "Org Preexistente"},
    )
    await conn.execute(
        sa.text(
            "INSERT INTO roles (id, organization_id, name, permissions) "
            "VALUES (:id, :org_id, 'owner', :permissions)"
        ).bindparams(sa.bindparam("permissions", type_=sa.JSON)),
        {
            "id": str(role_id),
            "org_id": str(org_id),
            "permissions": ["landlord:manage", "renter:manage"],
        },
    )
    return org_id, role_id


async def _get_permissions(conn: AsyncConnection, role_id: uuid.UUID) -> list[str]:
    """`jsonb_array_elements_text` deja que Postgres desarme el array --
    evita la ambiguedad de si el driver devuelve el JSONB crudo como
    `str` o ya decodificado como `list` (visto empiricamente variar
    entre ejecuciones con `sa.text` + `SELECT permissions` directo)."""
    result = await conn.execute(
        sa.text(
            "SELECT jsonb_array_elements_text(permissions) FROM roles WHERE id = :id"
        ),
        {"id": str(role_id)},
    )
    return [row[0] for row in result]


async def test_ca_r50_02_backfill_adds_permission_to_existing_owner_role():
    engine = get_engine()
    async with engine.connect() as conn:
        _org_id, role_id = await _seed_stale_owner_role(conn)
        await conn.execute(sa.text(_UPGRADE_SQL))
        permissions = await _get_permissions(conn, role_id)
        await conn.rollback()

    assert _PERMISSION in permissions
    assert set(permissions) == {"landlord:manage", "renter:manage", _PERMISSION}


async def test_ca_r50_02_backfill_is_idempotent_on_second_run():
    engine = get_engine()
    async with engine.connect() as conn:
        _org_id, role_id = await _seed_stale_owner_role(conn)
        await conn.execute(sa.text(_UPGRADE_SQL))
        await conn.execute(sa.text(_UPGRADE_SQL))  # re-ejecucion, ej: alembic upgrade head de nuevo
        permissions = await _get_permissions(conn, role_id)
        await conn.rollback()

    assert permissions.count(_PERMISSION) == 1


async def test_ca_r50_02_backfill_does_not_touch_admin_or_maintenance_roles():
    """Solo `owner` recibe el permiso -- `admin`/`maintenance` quedan
    intactos (sdd_03 v1.5: exclusivo de owner)."""
    engine = get_engine()
    async with engine.connect() as conn:
        org_id, _owner_role_id = await _seed_stale_owner_role(conn)
        admin_role_id = uuid.uuid4()
        await conn.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        await conn.execute(
            sa.text(
                "INSERT INTO roles (id, organization_id, name, permissions) "
                "VALUES (:id, :org_id, 'admin', :permissions)"
            ).bindparams(sa.bindparam("permissions", type_=sa.JSON)),
            {
                "id": str(admin_role_id),
                "org_id": str(org_id),
                "permissions": ["landlord:manage"],
            },
        )

        await conn.execute(sa.text(_UPGRADE_SQL))
        admin_permissions = await _get_permissions(conn, admin_role_id)
        await conn.rollback()

    assert _PERMISSION not in admin_permissions


async def test_ca_r50_02_downgrade_removes_only_the_added_permission():
    engine = get_engine()
    async with engine.connect() as conn:
        _org_id, role_id = await _seed_stale_owner_role(conn)
        await conn.execute(sa.text(_UPGRADE_SQL))
        await conn.execute(sa.text(_DOWNGRADE_SQL))
        permissions = await _get_permissions(conn, role_id)
        await conn.rollback()

    assert _PERMISSION not in permissions
    assert set(permissions) == {"landlord:manage", "renter:manage"}
