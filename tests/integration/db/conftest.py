"""Fixtures compartidas de `tests/integration/db`.

`adminprop.db.session.get_engine`/`get_session_factory` estan cacheadas
con `lru_cache` a nivel de proceso (una sola pool de conexiones en
runtime). pytest-asyncio por default crea un event loop nuevo por cada
test async — reusar el engine cacheado entre tests hace que sus
conexiones asyncpg queden atadas a un loop que ya cerro ("Future
attached to a different loop"). Este fixture fuerza una engine nueva
(y su dispose) en cada test de este paquete.
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
