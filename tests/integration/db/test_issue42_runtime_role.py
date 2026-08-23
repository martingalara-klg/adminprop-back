"""Issue #42 — security: el runtime debe conectar como adminprop_app para
que RLS sea efectivo.

Contexto: hasta este issue, el pool de la API/workers conectaba con el
superusuario de Postgres (default del compose local) -- los superusuarios
bypassean RLS incondicionalmente, asi que el aislamiento fisico
multi-tenant (sdd_04 §2.3, RN-D01) nunca estaba efectivamente activo en
runtime, solo la defensa app-level (filtro `organization_id` explicito en
los repositorios). Ademas, `SET ROLE adminprop_superadmin` era un no-op
funcional (el superusuario ya bypasseaba RLS antes y despues del cambio de
rol, sin restriccion que conmutar).

Requiere Postgres real con `alembic upgrade head` corrido, igual que el
resto de `tests/integration/db/`.

SDD: core/sdd_04_nonfunctional.md §2.3
     + infrastructure/spec_data_model.md §Principios Arquitectonicos
Implements: los 4 criterios de aceptacion del issue #42 (no tienen
            numeracion CA-XX de un SDD de features -- se nombran
            test_ca_42_0N_ siguiendo la convencion del repo igual).
"""

import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from adminprop.db.session import get_session_factory, get_superadmin_db_session, set_tenant_context

# `asyncio_mode = auto` (pyproject.toml) ya trata cada `async def test_...`
# como asyncio -- no se declara `pytestmark = pytest.mark.asyncio` a nivel
# de modulo a proposito: este archivo tiene un test sincronico
# (`test_ca_42_01_alembic_sigue_usando_el_superusuario_para_migrar`, lee
# texto de archivo, no conecta a la DB) que un `pytestmark` global
# marcaria incorrectamente como asyncio (warning de pytest-asyncio).


async def test_ca_42_01_runtime_pool_conecta_como_adminprop_app():
    """CA-42-01: el pool de runtime (`get_session_factory()`/`get_engine()`,
    lo que usan la API y los workers Celery) conecta como `adminprop_app`,
    nunca como el superusuario de Postgres.
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        current_user = (await session.execute(sa.text("SELECT current_user"))).scalar_one()
        is_superuser = (
            await session.execute(
                sa.text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            )
        ).scalar_one()

    assert current_user == "adminprop_app"
    assert is_superuser is False


async def test_ca_42_01_adminprop_app_no_tiene_bypassrls():
    """CA-42-01 (complemento): `adminprop_app` no tiene el atributo
    BYPASSRLS -- si lo tuviera, todas las politicas RLS `FORCE` de las
    tablas tenant-scoped serian irrelevantes para el runtime, exactamente
    el mismo problema que el superusuario.
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        rolbypassrls = (
            await session.execute(
                sa.text("SELECT rolbypassrls FROM pg_roles WHERE rolname = 'adminprop_app'")
            )
        ).scalar_one()

    assert rolbypassrls is False


def test_ca_42_01_alembic_sigue_usando_el_superusuario_para_migrar():
    """CA-42-01 (complemento): Alembic (`db/migrations/env.py`) resuelve su
    URL de conexion desde `Settings.migrations_database_url`, no desde
    `Settings.database_url` (la del runtime) -- separacion explicita para
    que el rol de runtime restringido (`adminprop_app`) nunca necesite
    privilegios DDL.

    No se importa `db/migrations/env.py` directamente: ese modulo ejecuta
    `config = context.config` a nivel de import, que solo existe dentro de
    una invocacion real de Alembic (`alembic upgrade head`) -- importarlo
    fuera de ese contexto revienta con `AttributeError`. Se verifica el
    codigo fuente en texto en su lugar; el comportamiento real (Alembic
    conectando con el superusuario) ya esta probado empiricamente por
    `alembic upgrade head` corriendo verde en CI/local con
    `MIGRATIONS_DATABASE_URL` seteada al superusuario.
    """
    repo_root = Path(__file__).resolve().parents[3]
    env_source = (repo_root / "src" / "adminprop" / "db" / "migrations" / "env.py").read_text(
        encoding="utf-8"
    )

    assert "migrations_database_url" in env_source
    assert "return get_settings().database_url" not in env_source


async def test_ca_42_02_set_role_adminprop_superadmin_funciona_de_verdad():
    """CA-42-02: `SET ROLE adminprop_superadmin` (lo que hace
    `get_superadmin_db_session`, la dependency de `/superadmin/*`) es
    funcional de verdad -- eleva el rol de sesion desde `adminprop_app` y
    lo revierte al salir, en vez de ser un no-op como cuando el runtime
    conectaba con el superusuario.
    """
    session_gen = get_superadmin_db_session()
    session = await anext(session_gen)
    try:
        elevated_user = (await session.execute(sa.text("SELECT current_user"))).scalar_one()
        assert elevated_user == "adminprop_superadmin"
    finally:
        # Agota el generador para que corra el `finally: RESET ROLE` de
        # `get_superadmin_db_session` (mismo patron que usaria FastAPI al
        # cerrar la dependency al final del request).
        await session_gen.aclose()

    # Una sesion NUEVA (pool de runtime normal) sigue siendo adminprop_app:
    # el `SET ROLE` de la sesion anterior no se filtro a otras conexiones.
    session_factory = get_session_factory()
    async with session_factory() as fresh_session:
        current_user = (await fresh_session.execute(sa.text("SELECT current_user"))).scalar_one()
    assert current_user == "adminprop_app"


async def test_ca_42_02_adminprop_app_sin_membresia_no_podria_elevar_rol():
    """CA-42-02 (regresion): si `adminprop_app` no fuera miembro de
    `adminprop_superadmin` (migracion `20260823_090000_grant_superadmin_
    role_to_app`), `SET ROLE adminprop_superadmin` fallaria con
    "permission denied to set role" -- se verifica positivamente que la
    membresia esta activa consultando el catalogo, en vez de solo confiar
    en que el test anterior no lanzo excepcion.
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        # 'MEMBER' (no 'USAGE'): la migracion otorga la membresia con
        # WITH INHERIT FALSE a proposito (ver su docstring) -- 'USAGE'
        # verificaria herencia automatica de privilegios, que
        # deliberadamente NO se otorga. 'MEMBER' es el chequeo correcto
        # para "puede hacer SET ROLE a", independiente de INHERIT.
        is_member = (
            await session.execute(
                sa.text("SELECT pg_has_role('adminprop_app', 'adminprop_superadmin', 'MEMBER')")
            )
        ).scalar_one()

    assert is_member is True


async def test_ca_42_03_rls_enforza_aislamiento_sin_filtro_app_level():
    """CA-42-03: conectado como `adminprop_app`, una query SIN ningun
    filtro `WHERE organization_id = ...` (sin la defensa app-level de los
    repositorios) solo devuelve las filas del tenant activo -- prueba de
    que el aislamiento lo hace RLS por si solo, no el filtro explicito.

    Usa `organizations`/`roles` (Capa 0, siempre presentes) para no
    depender de fixtures de otros modulos. `roles` tiene RLS FORCE
    (migracion `20260812_212704_create_capa0_fundacion.py`).
    """
    session_factory = get_session_factory()

    org_a = uuid.uuid4()
    org_b = uuid.uuid4()
    try:
        async with session_factory() as setup_session, setup_session.begin():
            # Crear organizations/roles requiere bypass: adminprop_app solo
            # tiene SELECT sobre `organizations` (RN-D: solo Super Admin
            # gestiona organizaciones).
            await setup_session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
            await setup_session.execute(
                sa.text(
                    "INSERT INTO organizations (id, slug, name) "
                    "VALUES (:id_a, :slug_a, 'Org A issue42'), (:id_b, :slug_b, 'Org B issue42')"
                ),
                {
                    "id_a": str(org_a),
                    "slug_a": f"org-a-42-{org_a.hex[:8]}",
                    "id_b": str(org_b),
                    "slug_b": f"org-b-42-{org_b.hex[:8]}",
                },
            )
            await setup_session.execute(
                sa.text(
                    "INSERT INTO roles (organization_id, name, permissions) "
                    "VALUES (:org_a, 'owner', '[]'::jsonb), (:org_b, 'owner', '[]'::jsonb)"
                ),
                {"org_a": str(org_a), "org_b": str(org_b)},
            )

            # La query bajo prueba: SIN WHERE organization_id, conectado como
            # adminprop_app real (no bypass), solo con el tenant context seteado.
        async with session_factory() as session, session.begin():
            await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
            await set_tenant_context(session, org_a)
            result = await session.execute(sa.text("SELECT organization_id FROM roles"))
            seen = {row[0] for row in result}

        assert seen == {org_a}
        assert org_b not in seen
    finally:
        async with session_factory() as cleanup_session, cleanup_session.begin():
            await cleanup_session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
            await cleanup_session.execute(
                sa.text("DELETE FROM roles WHERE organization_id IN (:a, :b)"),
                {"a": str(org_a), "b": str(org_b)},
            )
            await cleanup_session.execute(
                sa.text("DELETE FROM organizations WHERE id IN (:a, :b)"),
                {"a": str(org_a), "b": str(org_b)},
            )


async def test_ca_42_04_audit_logs_grants_no_incluyen_update_ni_delete():
    """CA-42-04: grants minimos documentados en la migracion -- `audit_logs`
    (append-only, RN-D03) no tiene UPDATE ni DELETE para `adminprop_app`
    (REVOKE del issue #10, `20260814_190741_create_audit_logs.py`), solo
    SELECT e INSERT. Se verifica contra el catalogo real de Postgres, no
    solo leyendo el SQL de la migracion.
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            sa.text(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE table_name = 'audit_logs' AND grantee = 'adminprop_app'"
            )
        )
        privileges = {row[0] for row in result}

    assert privileges == {"SELECT", "INSERT"}
    assert "UPDATE" not in privileges
    assert "DELETE" not in privileges


async def test_ca_42_04_adminprop_app_solo_puede_leer_y_actualizar_organizations():
    """CA-42-04: `organizations` (raiz, sin RLS) permite SELECT + UPDATE a
    `adminprop_app` (UPDATE: `OrganizationSettingsService`, issue #9 --
    owner/admin del tenant actualiza `settings`), pero NO INSERT ni
    DELETE -- crear/borrar organizaciones es exclusivo de `/superadmin/*`
    (rol `adminprop_superadmin`). Migraciones
    `20260812_212704_create_capa0_fundacion.py` +
    `20260823_090000_grant_superadmin_role_to_app.py` (REVOKE puntual).
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            sa.text(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE table_name = 'organizations' AND grantee = 'adminprop_app'"
            )
        )
        privileges = {row[0] for row in result}

    assert privileges == {"SELECT", "UPDATE"}


async def test_ca_42_04_intentar_insertar_organizations_como_adminprop_app_falla():
    """CA-42-04 (comportamiento, no solo catalogo): un INSERT directo en
    `organizations` conectado como `adminprop_app` sin elevar el rol falla
    por permisos -- confirma que el grant restringido de arriba se
    enforza de verdad, no solo que esta documentado.
    """
    session_factory = get_session_factory()
    with pytest.raises(DBAPIError):
        async with session_factory() as session, session.begin():
            bogus_id = uuid.uuid4()
            await session.execute(
                sa.text("INSERT INTO organizations (id, slug, name) VALUES (:id, :slug, 'nope')"),
                {"id": str(bogus_id), "slug": f"nope-{bogus_id.hex[:8]}"},
            )
