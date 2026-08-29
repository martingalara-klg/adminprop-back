"""Issue #3 — Alembic + roles PostgreSQL + hook RLS de sesion.

Requiere Postgres real (levantado por `docker/docker-compose.yml` local o
por el servicio `postgres` de `.github/workflows/ci.yml`) con
`alembic upgrade head` ya corrido (Makefile `migrate` / paso de CI "Run
Alembic migrations") antes de esta suite — mismo patron que el resto de
`tests/integration`.

SDD: infrastructure/spec_data_model.md §Principios Arquitectonicos
     + core/sdd_04_nonfunctional.md §2.3
"""

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_engine

pytestmark = pytest.mark.asyncio


async def test_ca_3_01_alembic_upgrade_head_deja_la_base_en_la_revision_actual():
    """CA #3-01: `alembic upgrade head` corre correctamente en el compose.

    La revision "head" avanza con cada migracion nueva (issue #116 agrego
    `20260829_090000` encadenada via `down_revision` a `20260828_130000`
    (issue #105), que a su vez fue agregada encima de `20260828_123003`
    (issue #103)) — el valor esperado se actualiza junto con la ultima
    migracion del repo; ver `tests/integration/db/test_capa2_propiedades.py`
    para la cobertura especifica del CHECK de `property_type`.
    """
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(sa.text("SELECT version_num FROM alembic_version"))
        version = result.scalar_one()
    assert version == "20260829_090000"


async def test_ca_3_01_extensiones_pgcrypto_y_btree_gist_quedan_habilitadas():
    """CA #3-01: la migracion deja pgcrypto y btree_gist instaladas (idempotente)."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(sa.text("SELECT extname FROM pg_extension"))
        installed = {row[0] for row in result}
    assert {"pgcrypto", "btree_gist"} <= installed


async def test_ca_3_02_rol_adminprop_app_existe_sin_bypassrls():
    """CA #3-02: adminprop_app existe, no tiene BYPASSRLS (sujeto a RLS)."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT rolcanlogin, rolbypassrls, rolsuper "
                "FROM pg_roles WHERE rolname = 'adminprop_app'"
            )
        )
        row = result.one()
    assert row.rolcanlogin is True
    assert row.rolbypassrls is False
    assert row.rolsuper is False


async def test_ca_3_02_rol_adminprop_superadmin_existe_con_bypassrls():
    """CA #3-02: adminprop_superadmin existe con BYPASSRLS (Decision #42)."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT rolcanlogin, rolbypassrls, rolsuper "
                "FROM pg_roles WHERE rolname = 'adminprop_superadmin'"
            )
        )
        row = result.one()
    assert row.rolcanlogin is True
    assert row.rolbypassrls is True
    assert row.rolsuper is False


async def test_ca_3_02_ambos_roles_tienen_connect_y_usage_sobre_el_schema_public():
    """CA #3-02: los roles pueden conectarse a la base y usar el schema public
    (permisos base para que las tablas de negocio del issue #5 sean alcanzables).
    """
    engine = get_engine()
    async with engine.connect() as conn:
        connect_result = await conn.execute(
            sa.text(
                "SELECT rolname FROM pg_roles "
                "WHERE rolname IN ('adminprop_app', 'adminprop_superadmin') "
                "AND has_database_privilege(rolname, current_database(), 'CONNECT')"
            )
        )
        can_connect = {row[0] for row in connect_result}

        usage_result = await conn.execute(
            sa.text(
                "SELECT rolname FROM pg_roles "
                "WHERE rolname IN ('adminprop_app', 'adminprop_superadmin') "
                "AND has_schema_privilege(rolname, 'public', 'USAGE')"
            )
        )
        can_use_schema = {row[0] for row in usage_result}

    assert can_connect == {"adminprop_app", "adminprop_superadmin"}
    assert can_use_schema == {"adminprop_app", "adminprop_superadmin"}


async def test_ca_3_01_correr_upgrade_head_de_nuevo_es_un_no_op_idempotente():
    """CA #3-01: `alembic upgrade head` re-ejecutado no falla (Alembic ya esta
    en head; y la migracion en si es idempotente si algun entorno la re-aplicara).
    """
    engine = get_engine()
    async with engine.connect() as conn:
        # CREATE EXTENSION IF NOT EXISTS + los DO-blocks de creacion de rol
        # (chequean pg_roles) son la garantia de idempotencia — se ejerce
        # ejecutando el mismo DDL de la migracion una segunda vez.
        await conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        await conn.execute(
            sa.text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'adminprop_app'
                    ) THEN
                        CREATE ROLE adminprop_app LOGIN;
                    END IF;
                END
                $$;
                """
            )
        )
        await conn.commit()

    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text("SELECT count(*) FROM pg_roles WHERE rolname = 'adminprop_app'")
        )
        assert result.scalar_one() == 1
