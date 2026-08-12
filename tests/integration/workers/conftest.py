"""Fixtures compartidas de `tests/integration/workers`.

Mismo motivo que `tests/integration/db/conftest.py` (issue #3):
`adminprop.db.session.get_engine`/`get_session_factory` estan cacheadas
con `lru_cache` a nivel de proceso; pytest-asyncio crea un event loop
nuevo por test, asi que reusar el engine cacheado entre tests ata sus
conexiones asyncpg a un loop ya cerrado.
"""

from collections.abc import AsyncGenerator

import pytest

from adminprop.db.session import get_engine, get_session_factory


@pytest.fixture(autouse=True)
async def _fresh_engine_per_test() -> AsyncGenerator[None]:
    get_session_factory.cache_clear()
    get_engine.cache_clear()
    yield
    engine = get_engine()
    await engine.dispose()
    get_session_factory.cache_clear()
    get_engine.cache_clear()
