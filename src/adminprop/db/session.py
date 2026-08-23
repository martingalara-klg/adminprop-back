"""Session factory async + hook de aislamiento multi-tenant (RLS).

SDD:
- infrastructure/spec_data_model.md §Principios Arquitectonicos
  ("El backend setea el contexto al inicio de cada request:
  SET LOCAL app.current_tenant_id = '<jwt.org>'").
- core/sdd_04_nonfunctional.md §2.3 (aislamiento multi-tenant).
- docs/skills/tenant-isolation.md (las 5 invariantes de aislamiento).

Este modulo NO decide cuando se llama a `set_tenant_context` (eso lo hace
el middleware FastAPI, todavia no implementado, o cada worker Celery
explicitamente `docs/skills/tenant-isolation.md`/`async-worker.md`).
Provee el primitivo reutilizable: la sesion async + el helper que emite
`SET LOCAL app.current_tenant_id` dentro de la transaccion actual.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import lru_cache
from uuid import UUID

from fastapi import Depends
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from adminprop.config import get_settings
from adminprop.shared.tenant import get_current_tenant

# Setting de sesion Postgres que las politicas RLS leen via
# current_setting('app.current_tenant_id', true) (missing_ok=true).
TENANT_CONTEXT_SETTING = "app.current_tenant_id"


def _register_tenant_context_reapplication(
    session: AsyncSession, organization_id: UUID | None
) -> None:
    """Reemite `SET LOCAL app.current_tenant_id` al inicio de CADA
    transaccion de `session`, no solo la primera.

    Bug descubierto en el issue #42 al hacer RLS real (antes, con el pool
    conectando como superusuario, era invisible): `SET LOCAL` (is_local=
    true, la unica opcion segura para un pool no transaction-scoped hoy --
    ver `set_tenant_context`) vive SOLO en la transaccion donde se
    ejecuta. Varios services hacen `insert -> notifications.emit -> commit`
    y el router reutiliza la MISMA sesion para una query posterior (ej.
    `WorkOrderService.create` + `get_detail`); `AsyncSession` autocomienza
    una transaccion NUEVA despues de ese commit, sin el tenant context ->
    RLS ve NULL -> 0 filas -> 404 espurio con el tenant correcto. Los
    workers Celery (`documents_worker.py`, que hace commits intermedios de
    `status: pending -> processing -> completed`) tienen el mismo riesgo.

    El evento ORM `after_begin` corre al inicio de toda transaccion
    (la primera y cualquiera posterior a un commit/rollback dentro de la
    misma sesion) -- reemitir el SET LOCAL aca la hace resistente a
    cualquier commit intermedio, sin cambiar el modelo transaccional de
    los services/workers existentes.

    `isinstance` guard: los tests unitarios de este modulo pasan dobles
    (`AsyncMock`) en lugar de una `AsyncSession` real -- `session.sync_session`
    no existe en esos dobles y `event.listens_for` fallaria al registrar
    sobre un target invalido. Se salta silenciosamente en ese caso (el
    comportamiento real se verifica en integracion, con Postgres real).
    """
    if not isinstance(session, AsyncSession):
        return  # pragma: no cover -- solo se salta con dobles de test (mocks)

    tenant_id = str(organization_id) if organization_id is not None else ""

    @event.listens_for(session.sync_session, "after_begin")
    def _reapply_tenant_context(sync_session, transaction, connection) -> None:
        connection.execute(
            text("SELECT set_config(:setting, :tenant_id, true)"),
            {"setting": TENANT_CONTEXT_SETTING, "tenant_id": tenant_id},
        )


def to_async_dsn(database_url: str) -> str:
    """Normaliza un DATABASE_URL (sync, usado por Alembic) al driver async (asyncpg).

    Alembic usa el driver sincronico (`psycopg2`) declarado en `DATABASE_URL`
    (ver `db/migrations/env.py`); el runtime de la app necesita el driver
    async `asyncpg` para SQLAlchemy 2.0 async. Se deriva de la misma
    variable de entorno para no duplicar configuracion.
    """
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url
    if database_url.startswith("postgresql+psycopg2://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql+psycopg2://") :]
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://") :]
    return database_url


@lru_cache
def get_engine() -> AsyncEngine:
    """Engine async cacheado por proceso (una sola pool de conexiones)."""
    settings = get_settings()
    return create_async_engine(to_async_dsn(settings.database_url), pool_pre_ping=True)


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Session factory async cacheada, ligada al engine del proceso."""
    return async_sessionmaker(bind=get_engine(), expire_on_commit=False)


async def set_tenant_context(session: AsyncSession, organization_id: UUID | None) -> None:
    """Emite `SET LOCAL app.current_tenant_id` en la transaccion de `session`.

    RN-D01 (docs/skills/tenant-isolation.md invariante #2): debe llamarse
    ANTES de cualquier query tenant-scoped. `set_config(..., true)` con
    `is_local=true` es el equivalente parametrizable de `SET LOCAL`: el
    valor vive solo durante la transaccion actual (PgBouncer transaction-
    scoped no lo filtra entre requests).

    `organization_id=None` limpia el contexto seteando un string vacio
    (ej: rutas `/superadmin/*` donde el rol de sesion pasa a
    `adminprop_superadmin` con BYPASSRLS y no corresponde fijar un
    tenant). Postgres no permite volver un GUC a NULL real via
    `set_config` — por eso toda politica RLS que lea este setting DEBE
    envolver el cast en `NULLIF(current_setting(...), '')::uuid`
    (docs/skills/tenant-isolation.md / database-migration.md), no solo
    `current_setting(..., true)`: un string vacio explicito revienta el
    cast a uuid con error 500 en vez de negar el acceso con 0 filas.
    """
    await session.execute(
        text("SELECT set_config(:setting, :tenant_id, true)"),
        {
            "setting": TENANT_CONTEXT_SETTING,
            "tenant_id": str(organization_id) if organization_id is not None else "",
        },
    )


async def get_db_session() -> AsyncGenerator[AsyncSession]:
    """Dependency FastAPI: una sesion por request.

    No setea el tenant context aca: eso lo hace el middleware (issue
    futuro, requiere decodificar el JWT) llamando a `set_tenant_context`
    con el `organization_id` del token, antes del primer query del
    endpoint.
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        yield session


async def get_superadmin_db_session() -> AsyncGenerator[AsyncSession]:
    """Dependency FastAPI para `/superadmin/*`: sesion bajo el rol BYPASSRLS.

    docs/skills/tenant-isolation.md "Super Admin: rol DB privilegiado" +
    core/spec_module_00_superadmin.md RN-01/RN-06: el portal Super Admin
    opera sobre `organizations` (raiz, sin RLS) y sobre tablas tenant-scoped
    (`roles`, `organization_invitations`) sin un `organization_id` propio
    -- necesita `adminprop_superadmin` (BYPASSRLS) para poder leerlas/
    escribirlas a traves de organizaciones distintas.

    No existe todavia el middleware global que conmuta el rol de sesion
    segun el JWT (`tenant-isolation.md` lo describe como responsabilidad
    del middleware, issue futuro); esta dependency es el equivalente
    explicito para los endpoints `/superadmin/*` mientras tanto.

    El `SET ROLE`/`RESET ROLE` es funcional de verdad desde el issue #42:
    el pool de runtime conecta como `adminprop_app` (no el superusuario de
    Postgres), y la migracion `20260823_090000_grant_superadmin_role_to_app`
    le otorga membresia en `adminprop_superadmin` (con `INHERIT FALSE`,
    asi que el bypass de RLS solo aplica mientras el `SET ROLE` explicito
    de abajo este activo en la transaccion, nunca por herencia implicita
    en el resto de las queries de `adminprop_app`).
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(text("SET ROLE adminprop_superadmin"))
        try:
            yield session
        finally:
            await session.execute(text("RESET ROLE"))


@asynccontextmanager
async def tenant_scoped_session(
    organization_id: UUID | None,
) -> AsyncGenerator[AsyncSession]:
    """Session factory "por tarea" para workers Celery.

    docs/skills/tenant-isolation.md / async-worker.md: "los workers no
    tienen middleware que lo setee" — antes de cualquier query dentro del
    worker hay que llamar a `set_tenant_context` explicitamente. Este
    context manager abre la sesion, fija el contexto en la misma
    transaccion y la entrega lista para usar.

    Issue #42: tambien registra `_register_tenant_context_reapplication` --
    workers como `documents_worker.py` hacen commits intermedios (updates
    de `status: pending -> processing -> completed`) sobre la misma
    sesion; sin reemitir el contexto en cada transaccion nueva, las
    queries posteriores a esos commits pierden el tenant bajo RLS real.
    """
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        _register_tenant_context_reapplication(session, organization_id)
        await set_tenant_context(session, organization_id)
        yield session


async def get_tenant_db_session(
    organization_id: UUID = Depends(get_current_tenant),
) -> AsyncGenerator[AsyncSession]:
    """Dependency FastAPI para modulos tenant-scoped sin middleware global
    todavia (issue #9, primer modulo que necesita RLS activo con un
    tenant real resuelto desde el JWT).

    Setea `app.current_tenant_id` ANTES de la primera query del endpoint
    -- corre bajo el rol de sesion normal (`adminprop_app`, sujeto a RLS
    FORCE), a diferencia de `get_superadmin_db_session` (BYPASSRLS).

    Issue #42: ademas registra `_register_tenant_context_reapplication`
    para que el contexto se reemita en cualquier transaccion posterior a
    un `session.commit()` intermedio del service (`SET LOCAL` no
    sobrevive un commit) -- sin este registro, un service que hace
    insert + commit + otra query en la misma request ve 0 filas por RLS
    aunque el tenant sea el correcto.
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        _register_tenant_context_reapplication(session, organization_id)
        await set_tenant_context(session, organization_id)
        yield session
