"""Issue #3 — Alembic + roles + hook RLS de sesion.

Unit tests (sin Postgres real) del helper `set_tenant_context` y de la
normalizacion de DSN. La verificacion contra RLS real vive en
`tests/integration/db/test_tenant_isolation.py`.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from adminprop.db.session import (
    TENANT_CONTEXT_SETTING,
    get_db_session,
    get_engine,
    get_session_factory,
    set_tenant_context,
    tenant_scoped_session,
    to_async_dsn,
)


def test_ca_3_03_to_async_dsn_convierte_psycopg2_a_asyncpg():
    """CA #3-03: el DATABASE_URL de Alembic (psycopg2, sync) se traduce a asyncpg."""
    result = to_async_dsn("postgresql+psycopg2://adminprop:adminprop@localhost:5432/adminprop")
    assert result == "postgresql+asyncpg://adminprop:adminprop@localhost:5432/adminprop"


def test_ca_3_03_to_async_dsn_convierte_url_generico_a_asyncpg():
    """CA #3-03: un DATABASE_URL sin driver explicito tambien se normaliza a asyncpg."""
    result = to_async_dsn("postgresql://adminprop:adminprop@localhost:5432/adminprop")
    assert result == "postgresql+asyncpg://adminprop:adminprop@localhost:5432/adminprop"


def test_ca_3_03_to_async_dsn_es_idempotente_si_ya_es_asyncpg():
    """CA #3-03: un DSN ya en asyncpg no se modifica."""
    dsn = "postgresql+asyncpg://adminprop:adminprop@localhost:5432/adminprop"
    assert to_async_dsn(dsn) == dsn


def test_ca_3_03_to_async_dsn_deja_otros_esquemas_sin_tocar():
    """DSN con un esquema no reconocido (ej: sqlite en tests futuros) pasa igual."""
    dsn = "sqlite+aiosqlite:///:memory:"
    assert to_async_dsn(dsn) == dsn


@pytest.mark.asyncio
async def test_ca_3_03_set_tenant_context_emite_set_config_con_el_organization_id():
    """CA #3-03: `set_tenant_context` emite SET LOCAL (via set_config) con el tenant activo."""
    session = AsyncMock()
    organization_id = uuid4()

    await set_tenant_context(session, organization_id)

    session.execute.assert_awaited_once()
    (clause, params), _kwargs = session.execute.call_args
    assert str(clause) == "SELECT set_config(:setting, :tenant_id, true)"
    assert params == {"setting": TENANT_CONTEXT_SETTING, "tenant_id": str(organization_id)}


@pytest.mark.asyncio
async def test_ca_3_03_set_tenant_context_limpia_el_contexto_cuando_organization_id_es_none():
    """CA #3-03: organization_id=None (rutas /superadmin/*) limpia el setting."""
    session = AsyncMock()

    await set_tenant_context(session, None)

    (_clause, params), _kwargs = session.execute.call_args
    assert params == {"setting": TENANT_CONTEXT_SETTING, "tenant_id": ""}


def test_ca_3_03_get_engine_esta_cacheado_por_proceso():
    """La engine async es un singleton por proceso (lru_cache)."""
    get_engine.cache_clear()
    try:
        assert get_engine() is get_engine()
    finally:
        get_engine.cache_clear()


def test_ca_3_03_get_session_factory_esta_cacheada_por_proceso():
    """La session factory es un singleton por proceso (lru_cache)."""
    get_session_factory.cache_clear()
    try:
        assert get_session_factory() is get_session_factory()
    finally:
        get_session_factory.cache_clear()
        get_engine.cache_clear()


@pytest.mark.asyncio
async def test_ca_3_03_get_db_session_entrega_una_sesion_por_request(monkeypatch):
    """CA #3-03: la dependency de FastAPI abre y cierra una sesion por request."""
    fake_session = AsyncMock()

    class _FakeSessionCtx:
        async def __aenter__(self):
            return fake_session

        async def __aexit__(self, *exc_info):
            return False

    fake_factory = MagicMock(return_value=_FakeSessionCtx())
    monkeypatch.setattr("adminprop.db.session.get_session_factory", lambda: fake_factory)

    sessions = [session async for session in get_db_session()]

    assert sessions == [fake_session]
    fake_factory.assert_called_once()


@pytest.mark.asyncio
async def test_ca_3_03_tenant_scoped_session_setea_el_contexto_antes_de_entregar_la_sesion(
    monkeypatch,
):
    """CA #3-03: la session factory "por tarea" (workers) llama set_tenant_context
    antes de entregar la sesion — 'los workers no tienen middleware que lo setee'
    (docs/skills/tenant-isolation.md / async-worker.md).
    """
    fake_session = AsyncMock()

    class _FakeTransactionCtx:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc_info):
            return False

    fake_session.begin = MagicMock(return_value=_FakeTransactionCtx())

    class _FakeSessionCtx:
        async def __aenter__(self):
            return fake_session

        async def __aexit__(self, *exc_info):
            return False

    fake_factory = MagicMock(return_value=_FakeSessionCtx())
    monkeypatch.setattr("adminprop.db.session.get_session_factory", lambda: fake_factory)

    organization_id = uuid4()
    async with tenant_scoped_session(organization_id) as session:
        assert session is fake_session

    (_clause, params), _kwargs = fake_session.execute.call_args
    assert params == {"setting": TENANT_CONTEXT_SETTING, "tenant_id": str(organization_id)}


def test_ca_3_03_base_es_una_declarative_base_para_futuros_modelos():
    """CA #3-03: `db/migrations/env.py` usa `Base.metadata` como target_metadata.

    Actualizado en el issue #13 (`modules/people/models.py`): `Landlord`/
    `Renter` son los primeros modelos ORM reales del repo, tal como
    `db/base.py` lo anticipaba ("Base declarativa para futuros modelos
    ORM de adminprop") -- la aserción original ("metadata vacia") asumía
    que ningún modulo declaraba modelos todavia (issue #5); ya no es el
    caso. El proyecto sigue sin usar `--autogenerate` (SQL crudo con
    `op.execute`, `docs/skills/database-migration.md`), por eso solo se
    verifica que las tablas de `people` estén registradas, no que
    coincidan con el DDL real.
    """
    from sqlalchemy.orm import DeclarativeBase

    from adminprop.db.base import Base
    from adminprop.modules.people import (
        models as people_models,  # noqa: F401 -- registra las tablas en Base.metadata
    )

    assert issubclass(Base, DeclarativeBase)
    assert {"landlords", "renters"} <= set(Base.metadata.tables)
