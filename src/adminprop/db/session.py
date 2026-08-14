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
from sqlalchemy import text
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
    explicito para los endpoints `/superadmin/*` mientras tanto. El
    `SET ROLE`/`RESET ROLE` es hoy un no-op funcional en este entorno
    (issue #42 -- PgBouncer transaction-scoped todavia no esta configurado
    en docker-compose local) pero se aplica igual para que el codigo quede
    correcto cuando la infra lo soporte.
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
    """
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
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
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        await set_tenant_context(session, organization_id)
        yield session
