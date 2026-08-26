"""Issue #89 — regresion del import circular que rompia el boot de Celery.

SDD: core/sdd_04_nonfunctional.md §1.3 (workers canonicos).
Skill: docs/skills/async-worker.md.

`notification_worker.py` -> `payments.service` -> `administracion.repository`
-> `administracion/__init__.py` -> `administracion/router.py` ->
`administracion/service.py` -> de vuelta a `notification_worker.py`
(`send_transactional_email`, a nivel de modulo) cerraba un ciclo que
revienta CUALQUIER `celery -A adminprop.workers.celery_app worker|beat`
con `ImportError: cannot import name 'send_transactional_email' from
partially initialized module` -- el traceback exacto del issue #89.

Este bug fue invisible para el resto de la suite porque los tests
invocan las tareas de Celery en modo directo (`task.__wrapped__(...)` o
llamando la funcion python normal) dentro del proceso de pytest, donde
`adminprop.modules.administracion.service` y
`adminprop.workers.notification_worker` casi siempre ya estan cacheados
en `sys.modules` en un orden que no dispara el ciclo -- nunca se bootea
un worker Celery real. Por eso este test corre en un INTERPRETE PYTHON
FRESCO (subprocess), sin nada precacheado, replicando el import que hace
el `Loader` de Celery al arrancar (`include=[...]` en `celery_app.py`) --
`celery_app.loader.import_default_modules()` es el mismo mecanismo que
`celery -A adminprop.workers.celery_app worker|beat` dispara al boot,
sin necesitar broker/DB corriendo (la conexion a Redis es lazy).
"""

import subprocess
import sys


def _run_fresh_python(code: str) -> subprocess.CompletedProcess[str]:
    """Corre `code` en un interprete Python nuevo con `src` en el path,
    igual que `pythonpath = ["src"]` de pyproject.toml para pytest."""
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd="src",
        capture_output=True,
        text=True,
        timeout=60,
        check=False,  # el returncode se afirma explicitamente en cada test
    )


def test_issue_89_notification_worker_then_documents_worker_import_clean():
    """Reproduce el traceback EXACTO del issue #89: importar
    `notification_worker` primero (el orden real de `celery_app.py`
    `include=[...]`) y despues `documents_worker`, en un interprete
    limpio -- antes del fix, esto fallaba con `ImportError:
    cannot import name 'send_transactional_email' from partially
    initialized module 'adminprop.workers.notification_worker'`."""
    result = _run_fresh_python(
        "import adminprop.workers.notification_worker\n"
        "import adminprop.workers.documents_worker\n"
        "print('IMPORT_OK')\n"
    )

    assert result.returncode == 0, result.stderr
    assert "ImportError" not in result.stderr
    assert "IMPORT_OK" in result.stdout


def test_issue_89_celery_loader_import_default_modules_boots_clean():
    """Simula el boot REAL de `celery -A adminprop.workers.celery_app
    worker|beat`: `Loader.import_default_modules()` es el mecanismo que
    Celery usa internamente para importar `conf.include` -- exactamente
    lo que corre al levantar `documents_worker`, `notification_worker` o
    `beat` (docker/docker-compose.yml, profile "workers"). No requiere
    broker ni DB corriendo: la conexion a Redis es lazy."""
    result = _run_fresh_python(
        "from adminprop.workers.celery_app import celery_app\n"
        "celery_app.loader.import_default_modules()\n"
        "print('BOOT_IMPORT_OK')\n"
    )

    assert result.returncode == 0, result.stderr
    assert "ImportError" not in result.stderr
    assert "BOOT_IMPORT_OK" in result.stdout


def test_issue_89_administracion_service_has_no_module_level_worker_import():
    """Guarda de regresion textual: `administracion/service.py` no debe
    volver a importar `send_transactional_email` (ni ningun simbolo de
    `adminprop.workers.*`) a nivel de modulo -- solo dentro de la funcion
    que lo usa (`_send_invitation_email`). Un test de import en
    subprocess ya cubre el sintoma; este cubre la causa para que un
    futuro revert silencioso (ej: un linter que "ordena" imports y los
    sube al tope del archivo) se detecte sin necesitar bootear Celery."""
    import ast
    from pathlib import Path

    service_path = (
        Path(__file__).parents[3]
        / "src"
        / "adminprop"
        / "modules"
        / "administracion"
        / "service.py"
    )
    tree = ast.parse(service_path.read_text(encoding="utf-8"))

    module_level_worker_imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "adminprop.workers.notification_worker"
    ]

    assert module_level_worker_imports == []
