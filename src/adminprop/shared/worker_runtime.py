"""Ciclo de vida de los recursos async compartidos DENTRO de una tarea
Celery (issue #93).

Bug reproducido (traceback exacto en `.superpowers/issue-93-report.md`):
`adminprop.db.session.get_engine`/`get_session_factory` y
`adminprop.shared.cache.redis.get_redis_client` estan `@lru_cache` a
nivel de PROCESO -- correcto para FastAPI (un unico event loop durante
toda la vida del proceso, `uvicorn`), pero cada tarea Celery
(`documents_worker.py`/`notification_worker.py`) hace su propio
`asyncio.run(...)`, que crea y CIERRA un event loop nuevo por invocacion.

Las conexiones async (asyncpg vía SQLAlchemy, `redis.asyncio`) quedan
atadas al loop en el que se crearon la primera vez. Un worker Celery de
larga vida (prefork, sin `worker_max_tasks_per_child`) procesa muchas
tareas en el MISMO proceso: la primera tarea crea el engine/cliente
(lazy) atado a su loop; la segunda tarea corre en un loop nuevo pero
reusa el objeto cacheado -> `RuntimeError: Future attached to a
different loop` / `RuntimeError: Event loop is closed` en cuanto ese
objeto intenta usar su conexion vieja.

Fix: cada tarea Celery ejecuta su coroutine principal envuelta en
`run_worker_coroutine`, que -- DENTRO del mismo loop que `asyncio.run()`
va a cerrar -- dispone el engine y cierra el cliente Redis, y limpia el
cache de los tres `lru_cache` al terminar (exito o excepcion). La
proxima tarea, con el cache vacio, crea recursos nuevos atados
unicamente al loop nuevo que le toca. Mismo patron que ya usa
`tests/integration/workers/conftest.py`/`tests/integration/db/conftest.py`
para aislar tests async entre si (pytest-asyncio tambien crea un loop
por test) -- este modulo lo aplica al runtime real de los workers, no
solo a los tests.
"""

from __future__ import annotations

import logging
from collections.abc import Coroutine
from typing import TypeVar

from adminprop.db.session import get_engine, get_session_factory
from adminprop.shared.cache.redis import get_redis_client

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


async def run_worker_coroutine(coro: Coroutine[object, object, _T]) -> _T:
    """Punto de entrada UNICO para el `asyncio.run(...)` de una tarea
    Celery: ejecuta `coro` y libera los recursos async cacheados por
    proceso (engine SQLAlchemy + cliente Redis) al terminar, todavia
    dentro del mismo loop -- ver docstring del modulo.

    Uso: `asyncio.run(run_worker_coroutine(_xxx_async(...)))` en cada
    tarea de `documents_worker.py`/`notification_worker.py`. El valor de
    retorno y las excepciones de `coro` se propagan sin modificar (el
    cleanup corre en un `finally`, nunca oculta un resultado ni una
    excepcion real de la tarea).
    """
    try:
        return await coro
    finally:
        await get_engine().dispose()
        try:
            await get_redis_client().aclose()
        except Exception:
            # Best-effort: un fallo cerrando el cliente Redis no debe
            # tapar el resultado/excepcion real de `coro`, que ya se
            # esta propagando (o ya se retorno) en este punto.
            logger.warning(
                "run_worker_coroutine: fallo cerrando el cliente Redis", exc_info=True
            )
        get_engine.cache_clear()
        get_session_factory.cache_clear()
        get_redis_client.cache_clear()
