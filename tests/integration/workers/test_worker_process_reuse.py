"""Issue #93 -- documents_worker/notification_worker crashean en la
SEGUNDA tarea del mismo proceso worker (reuso de event loop asyncio /
cliente Redis).

SDD: core/sdd_04_nonfunctional.md §1.3 (workers canonicos).
Skill: docs/skills/async-worker.md.

Repro (traceback exacto, `.superpowers/issue-93-report.md`): un worker
Celery de larga vida (`prefork`, sin `worker_max_tasks_per_child`)
procesa muchas tareas en el MISMO proceso -- cada tarea hace su propio
`asyncio.run(...)`, que abre y CIERRA un event loop nuevo. Los
singletons `@lru_cache` a nivel de proceso
(`adminprop.db.session.get_engine`/`get_session_factory`,
`adminprop.shared.cache.redis.get_redis_client`) crean sus conexiones
async (asyncpg / `redis.asyncio`) atadas al primer loop en el que se
usan; la SEGUNDA tarea corre en un loop nuevo pero reusa el objeto
cacheado -> `RuntimeError: ... got Future ... attached to a different
loop` seguido de `RuntimeError: Event loop is closed` en cuanto esa
conexion vieja intenta usarse (confirmado primero en
`job_status.set_job_status` via `get_redis_client`, mismo mecanismo
para `get_session_factory`).

No se puede reproducir esto invocando `asyncio.run()` dos veces dentro
del MISMO test de pytest-asyncio (pytest-asyncio ya corre CADA test en
su propio loop nuevo -- anidar `asyncio.run()` dentro de un loop que ya
esta corriendo revienta con un error distinto, no reproductivo del bug
real). Se necesita un interprete Python fresco que llame
`asyncio.run()` dos veces SEGUIDAS en el mismo proceso -- exactamente
lo que hace un worker Celery `prefork` de larga vida entre tarea y
tarea -- mismo patron de subprocess que
`tests/unit/workers/test_worker_boot_imports.py` (issue #89) usa para
aislar el boot de Celery de los modulos ya cacheados en `sys.modules`
del proceso de pytest.

Fix: `adminprop.shared.worker_runtime.run_worker_coroutine` dispone el
engine/cliente Redis y limpia los tres `lru_cache` al final de CADA
tarea, todavia dentro del loop que `asyncio.run()` esta por cerrar --
la proxima tarea, con el cache vacio, crea recursos nuevos atados
unicamente a su loop. `documents_worker.py`/`notification_worker.py`
envuelven ahi TODOS sus `asyncio.run(...)`.

Requiere Postgres/Redis reales alcanzables via `DATABASE_URL`/
`REDIS_URL` (heredadas del entorno del proceso de pytest -- mismo
mecanismo que `tests/unit/workers/test_worker_boot_imports.py`, sin
pasar `env=` explicito a `subprocess.run`). No siembra datos de negocio:
alcanza con UUIDs random (`settlement`/`organization`/`notification`
inexistentes) para ejercitar la capa de conexion -- exactamente el
mismo camino que revienta antes del fix, sin depender del resultado de
negocio de la tarea.
"""

from __future__ import annotations

import subprocess
import sys


def _run_fresh_python(code: str) -> subprocess.CompletedProcess[str]:
    """Corre `code` en un interprete Python nuevo con `src` en el path --
    mismo helper que `tests/unit/workers/test_worker_boot_imports.py`."""
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd="src",
        capture_output=True,
        text=True,
        timeout=60,
        check=False,  # el returncode se afirma explicitamente en cada test
    )


_LOOP_REUSE_ERRORS = ("attached to a different loop", "Event loop is closed")


def _assert_no_loop_reuse_crash(result: subprocess.CompletedProcess[str], marker: str) -> None:
    assert result.returncode == 0, result.stderr
    for needle in _LOOP_REUSE_ERRORS:
        assert needle not in result.stderr, result.stderr
    assert marker in result.stdout, result.stdout


def test_issue_93_generate_settlement_survives_two_tasks_in_the_same_process():
    """`documents_worker.generate_settlement` -- reproduce el traceback
    EXACTO del issue #93 (`Future attached to a different loop` /
    `Event loop is closed` en `job_status.set_job_status` via
    `get_redis_client`) al procesar dos liquidaciones seguidas en el
    mismo proceso worker. Antes del fix, la primera tarea completaba
    limpio y la segunda revienta -- exactamente el sintoma reportado por
    adminprop-front#39."""
    result = _run_fresh_python(
        "import asyncio, uuid\n"
        "from adminprop.shared.worker_runtime import run_worker_coroutine\n"
        "from adminprop.workers.documents_worker import _generate_settlement_async\n"
        "for i in range(2):\n"
        "    asyncio.run(run_worker_coroutine(\n"
        "        _generate_settlement_async(uuid.uuid4(), uuid.uuid4(), f'req-{i}')\n"
        "    ))\n"
        "print('TWO_GENERATE_SETTLEMENT_TASKS_OK')\n"
    )
    _assert_no_loop_reuse_crash(result, "TWO_GENERATE_SETTLEMENT_TASKS_OK")


def test_issue_93_regenerate_settlement_survives_two_tasks_in_the_same_process():
    """`documents_worker.regenerate_settlement` -- mismo mecanismo que
    `generate_settlement`, cubierto por separado porque tiene su propio
    `asyncio.run(...)` en `regenerate_settlement` (linea distinta del
    archivo, wrapper aplicado independientemente)."""
    result = _run_fresh_python(
        "import asyncio, uuid\n"
        "from adminprop.shared.worker_runtime import run_worker_coroutine\n"
        "from adminprop.workers.documents_worker import _regenerate_settlement_async\n"
        "for i in range(2):\n"
        "    asyncio.run(run_worker_coroutine(\n"
        "        _regenerate_settlement_async(\n"
        "            uuid.uuid4(), uuid.uuid4(), f'req-{i}', None, uuid.uuid4()\n"
        "        )\n"
        "    ))\n"
        "print('TWO_REGENERATE_SETTLEMENT_TASKS_OK')\n"
    )
    _assert_no_loop_reuse_crash(result, "TWO_REGENERATE_SETTLEMENT_TASKS_OK")


def test_issue_93_notification_worker_send_notification_email_survives_two_tasks():
    """`notification_worker.send_notification_email` -- mismo patron
    (`asyncio.run` + singletons `@lru_cache`) que `documents_worker.py`;
    el issue #93 pide comparar explicitamente contra `notification_worker`
    (activo hace mas tiempo) -- comparte la MISMA vulnerabilidad, nunca
    observada en operacion real por volumen bajo, no por un patron
    distinto."""
    result = _run_fresh_python(
        "import asyncio, uuid\n"
        "from adminprop.shared.worker_runtime import run_worker_coroutine\n"
        "from adminprop.workers.notification_worker import _send_notification_email_async\n"
        "for i in range(2):\n"
        "    asyncio.run(run_worker_coroutine(\n"
        "        _send_notification_email_async(uuid.uuid4(), uuid.uuid4(), f'req-{i}')\n"
        "    ))\n"
        "print('TWO_SEND_NOTIFICATION_EMAIL_TASKS_OK')\n"
    )
    _assert_no_loop_reuse_crash(result, "TWO_SEND_NOTIFICATION_EMAIL_TASKS_OK")


def test_issue_93_generate_rent_periods_beat_task_survives_two_runs_in_the_same_process():
    """`notification_worker.generate_rent_periods` (Celery Beat) -- mismo
    mecanismo, cubierto porque Beat reusa el MISMO proceso worker que
    consume la cola `notifications` entre corridas programadas."""
    result = _run_fresh_python(
        "import asyncio\n"
        "from adminprop.shared.worker_runtime import run_worker_coroutine\n"
        "from adminprop.workers.notification_worker import _generate_rent_periods_async\n"
        "for i in range(2):\n"
        "    asyncio.run(run_worker_coroutine(_generate_rent_periods_async(f'req-{i}')))\n"
        "print('TWO_GENERATE_RENT_PERIODS_RUNS_OK')\n"
    )
    _assert_no_loop_reuse_crash(result, "TWO_GENERATE_RENT_PERIODS_RUNS_OK")
