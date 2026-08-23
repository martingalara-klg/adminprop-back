"""App Celery: broker/result backend Redis + Beat (issue #4).

SDD: core/sdd_04_nonfunctional.md §1.3 — lista canonica de workers
(`notification_worker`, `documents_worker`) y tareas de Celery Beat
(`generate_rent_periods`, `detect_due_adjustments`, `detect_expiring_contracts`).
Skill: docs/skills/async-worker.md.

Se importa desde `docker/docker-compose.yml` via `celery -A
adminprop.workers.celery_app worker|beat ...` (issue #2 ya declaro esos
comandos bajo el profile "workers", en espera de este modulo).
"""

from celery import Celery
from celery.schedules import crontab

from adminprop.config import get_settings

_settings = get_settings()

celery_app = Celery(
    "adminprop",
    # Decision de implementacion (issue #4): un unico Redis (misma URL) para
    # broker y result backend en el MVP. El volumen de referencia de sdd_04
    # §1.2 (10-200 propiedades, 1-5 usuarios por org) no justifica una
    # segunda variable de entorno/instancia separada todavia; el result
    # backend solo trackea `task_id` operacional (async-worker.md), nunca
    # datos de negocio.
    broker=_settings.redis_url,
    backend=_settings.redis_url,
    include=[
        "adminprop.workers.notification_worker",
        "adminprop.workers.documents_worker",
    ],
)

celery_app.conf.update(
    task_acks_late=True,  # ack tras exito, no al recibir (async-worker.md)
    task_reject_on_worker_lost=True,  # re-encolar si el worker muere
    task_track_started=True,
    worker_prefetch_multiplier=1,  # sin hoarding; equidad entre workers
    broker_connection_retry_on_startup=True,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)

# ─── Enrutamiento a colas dedicadas (CA-4-01) ──────────────────────────────
# docker/docker-compose.yml (issue #2) ya declara `notification_worker`
# consumiendo solo la cola "notifications" (`-Q notifications`) y
# `documents_worker` solo "documents" (`-Q documents`). Sin este
# `task_routes`, Celery encolaria todo en la cola default "celery" y
# ningun worker la consumiria — los jobs quedarian atascados sin error
# visible. El patron de wildcard cubre las tareas de Beat (mismo modulo
# que send_transactional_email) sin listarlas una por una.
celery_app.conf.task_routes = {
    "adminprop.workers.notification_worker.*": {"queue": "notifications"},
    "adminprop.workers.documents_worker.*": {"queue": "documents"},
}

# ─── Celery Beat — sdd_04 §1.3 lista canonica de tareas programadas ────────
# Los horarios usan `crontab` (no el dict simplificado del skill, que no es
# un tipo de schedule valido para Celery real) para que Beat efectivamente
# dispare "1 de cada mes" / "diaria" en vez de interpretarse como un
# intervalo fijo.
celery_app.conf.beat_schedule = {
    "generate-rent-periods-monthly": {
        "task": "adminprop.workers.notification_worker.generate_rent_periods",
        # 1° de cada mes, 00:30 UTC — idempotente (RN-P01, issue #21).
        "schedule": crontab(minute=30, hour=0, day_of_month=1),
    },
    "detect-due-adjustments-daily": {
        "task": "adminprop.workers.notification_worker.detect_due_adjustments",
        # Diaria, 01:00 UTC — crea ajustes pending (RN-C03, issue #18).
        "schedule": crontab(minute=0, hour=1),
    },
    "detect-expiring-contracts-daily": {
        "task": "adminprop.workers.notification_worker.detect_expiring_contracts",
        # Diaria, 01:30 UTC — notifica contratos por vencer (issue #19).
        "schedule": crontab(minute=30, hour=1),
    },
}
