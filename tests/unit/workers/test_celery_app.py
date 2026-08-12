"""Issue #4 — App Celery: broker/backend, config de reintentos, Beat.

SDD: core/sdd_04_nonfunctional.md §1.3.
Skill: docs/skills/async-worker.md.
"""

from celery.schedules import crontab

from adminprop.config import get_settings
from adminprop.workers.celery_app import celery_app


def test_ca_4_01_broker_and_result_backend_are_configured():
    """CA-4-01: el broker y el result backend apuntan a Redis (settings)."""
    settings = get_settings()
    assert celery_app.conf.broker_url == settings.redis_url
    assert celery_app.conf.result_backend == settings.redis_url


def test_ca_4_01_includes_both_canonical_workers():
    """CA-4-01: notification_worker y documents_worker son las 2 colas
    canonicas (sdd_04 §1.3) — no existe un worker de indices (ingreso
    manual del porcentaje, sdd_03 §8)."""
    assert celery_app.conf.include == [
        "adminprop.workers.notification_worker",
        "adminprop.workers.documents_worker",
    ]


def test_retry_and_queue_conf_matches_async_worker_skill():
    """docs/skills/async-worker.md: configuracion base de colas/reintentos."""
    conf = celery_app.conf
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.task_track_started is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.broker_connection_retry_on_startup is True
    assert conf.task_serializer == "json"
    assert conf.accept_content == ["json"]
    assert conf.result_serializer == "json"
    assert conf.timezone == "UTC"
    assert conf.enable_utc is True


def test_ca_4_01_tasks_are_routed_to_the_dedicated_queues_declared_in_compose():
    """CA-4-01: `docker/docker-compose.yml` (issue #2) hace que
    notification_worker consuma solo la cola "notifications" y
    documents_worker solo "documents" (`-Q <cola>`). Sin este
    `task_routes`, las tareas caerian en la cola default "celery" que
    ningun worker consume — el job quedaria atascado sin error visible."""
    routes = celery_app.conf.task_routes

    assert routes["adminprop.workers.notification_worker.*"] == {"queue": "notifications"}
    assert routes["adminprop.workers.documents_worker.*"] == {"queue": "documents"}


def test_ca_4_04_beat_schedule_declares_the_three_canonical_tasks():
    """CA-4-04: Beat programa exactamente las 3 tareas canonicas de
    sdd_04 §1.3, con el horario documentado ahi."""
    schedule = celery_app.conf.beat_schedule

    assert set(schedule) == {
        "generate-rent-periods-monthly",
        "detect-due-adjustments-daily",
        "detect-expiring-contracts-daily",
    }

    rent_periods = schedule["generate-rent-periods-monthly"]
    assert rent_periods["task"] == "adminprop.workers.notification_worker.generate_rent_periods"
    assert rent_periods["schedule"] == crontab(minute=30, hour=0, day_of_month=1)

    due_adjustments = schedule["detect-due-adjustments-daily"]
    assert due_adjustments["task"] == (
        "adminprop.workers.notification_worker.detect_due_adjustments"
    )
    assert due_adjustments["schedule"] == crontab(minute=0, hour=1)

    expiring_contracts = schedule["detect-expiring-contracts-daily"]
    assert expiring_contracts["task"] == (
        "adminprop.workers.notification_worker.detect_expiring_contracts"
    )
    assert expiring_contracts["schedule"] == crontab(minute=30, hour=1)
