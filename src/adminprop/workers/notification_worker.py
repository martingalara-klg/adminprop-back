"""notification_worker — email transaccional (Resend) + Beat stubs (issue #4).

SDD: core/sdd_04_nonfunctional.md §1.3, infrastructure/spec_notificaciones.md §Email.
Skill: docs/skills/async-worker.md, docs/skills/external-integrations.md.
Implements: CA-4-02 (retry 30/90/270s + jitter, clasificacion Retryable/NonRetryable).

`send_transactional_email` es la tarea generica reutilizable: el
`NotificationService` real (issue #11, con la tabla `notifications` y el
patron outbox `email_sent_at IS NULL`) la encola despues del commit de la
operacion de negocio (RF-01 spec_notificaciones.md) — todavia no existe en
este issue.

Las 3 tareas de Celery Beat (`generate_rent_periods`, `detect_due_adjustments`,
`detect_expiring_contracts`) son stubs en este issue (CA-4-04): solo loguean
inicio/fin para validar que Beat las dispara end-to-end. La logica de
negocio real llega con los issues #21, #18 y #19 respectivamente.
"""

import asyncio
import logging
import random

from celery import Task

from adminprop.shared.email.sender import send_email
from adminprop.shared.errors.retryable import (
    NonRetryableNotificationError,
    RetryableNotificationError,
)
from adminprop.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# sdd_04 §1.3 / spec_notificaciones.md §"Apendice": backoff FIJO 30s -> 90s ->
# 270s (no exponencial de base 2) — por eso la tarea calcula el countdown a
# mano en vez de usar `retry_backoff` nativo de Celery (que solo modela
# progresiones de potencia de 2).
RETRY_DELAYS_SECONDS: tuple[int, ...] = (30, 90, 270)
MAX_RETRIES = len(RETRY_DELAYS_SECONDS)
# Jitter proporcional (equivalente a `retry_jitter=True` de Celery): hasta un
# 20% de variacion sobre el delay base, para evitar reintentos sincronizados
# en masa contra Resend.
_JITTER_RATIO = 0.2


def retry_countdown_seconds(retries: int) -> float:
    """Delay (en segundos) del proximo intento, con jitter.

    `retries` es `self.request.retries` (0 en el primer fallo, 1 en el
    segundo, ...); valores >= len(RETRY_DELAYS_SECONDS) usan el ultimo
    delay declarado (270s) como piso de seguridad.
    """
    base = RETRY_DELAYS_SECONDS[min(retries, len(RETRY_DELAYS_SECONDS) - 1)]
    jitter = random.uniform(0, base * _JITTER_RATIO)
    return base + jitter


class NotificationTask(Task):
    """Politica de reintentos de email — sdd_04 §1.3."""

    max_retries = MAX_RETRIES


@celery_app.task(
    base=NotificationTask,
    bind=True,
    name="adminprop.workers.notification_worker.send_transactional_email",
)
def send_transactional_email(
    self: Task,
    *,
    to: list[str],
    subject: str,
    html: str,
    organization_name: str,
    request_id: str,
    text: str | None = None,
    owner_reply_email: str | None = None,
) -> str | None:
    """Envia un email transaccional via Resend con la politica de reintentos.

    Implements: CA-4-02. RF-03 (spec_notificaciones.md): esta tarea se
    encola *despues* del commit de la operacion de negocio que la origina
    — un fallo aca nunca revierte ni bloquea esa operacion.

    Retorna el `message_id` de Resend si el envio tuvo exito, o `None` si
    se agotaron los reintentos (dead-letter, spec_notificaciones.md
    §"Apendice") o si el error es no-reintentable.
    """
    retries = self.request.retries
    attempt = retries + 1
    logger.info(
        "send_transactional_email start",
        extra={"request_id": request_id, "attempt": attempt, "service": "notification_worker"},
    )
    try:
        message_id = asyncio.run(
            send_email(
                to=to,
                subject=subject,
                html=html,
                text=text,
                organization_name=organization_name,
                owner_reply_email=owner_reply_email,
                request_id=request_id,
            )
        )
    except RetryableNotificationError as exc:
        logger.warning(
            "send_transactional_email retryable error",
            extra={"request_id": request_id, "attempt": attempt, "error": str(exc)},
        )
        if retries >= MAX_RETRIES:
            # Dead-letter (spec_notificaciones.md §"Apendice"): sin
            # reintento automatico posterior. El aviso in-app (issue #11)
            # ya cubre la necesidad; el fallo queda registrado con
            # request_id para diagnostico.
            logger.error(
                "send_transactional_email exhausted retries, dead-letter",
                extra={"request_id": request_id, "attempts": attempt, "error": str(exc)},
            )
            return None
        raise self.retry(
            exc=exc,
            countdown=retry_countdown_seconds(retries),
            max_retries=MAX_RETRIES,
        )
    except NonRetryableNotificationError as exc:
        logger.error(
            "send_transactional_email non-retryable, giving up",
            extra={"request_id": request_id, "attempt": attempt, "error": str(exc)},
        )
        return None

    logger.info(
        "send_transactional_email sent",
        extra={"request_id": request_id, "message_id": message_id, "attempt": attempt},
    )
    return message_id


# ─── Celery Beat — stubs (CA-4-04) ──────────────────────────────────────────
# Logica real: issue #21 (generate_rent_periods, RN-P01), issue #18
# (detect_due_adjustments, RN-C03), issue #19 (detect_expiring_contracts).


@celery_app.task(bind=True, name="adminprop.workers.notification_worker.generate_rent_periods")
def generate_rent_periods(self: Task) -> None:
    """Stub (issue #4): Beat dispara esta tarea el 1° de cada mes, 00:30 UTC.

    Logica real (generar el `rent_period` de cada contrato activo, RN-P01)
    llega con el issue #21 — sdd_04 §1.3.
    """
    logger.info(
        "generate_rent_periods stub — logica real llega con el issue #21",
        extra={"attempt": self.request.retries + 1, "service": "notification_worker"},
    )


@celery_app.task(bind=True, name="adminprop.workers.notification_worker.detect_due_adjustments")
def detect_due_adjustments(self: Task) -> None:
    """Stub (issue #4): Beat dispara esta tarea diariamente, 01:00 UTC.

    Logica real (crear ajustes `pending` + notificar, RN-C03) llega con el
    issue #18 — sdd_04 §1.3.
    """
    logger.info(
        "detect_due_adjustments stub — logica real llega con el issue #18",
        extra={"attempt": self.request.retries + 1, "service": "notification_worker"},
    )


@celery_app.task(bind=True, name="adminprop.workers.notification_worker.detect_expiring_contracts")
def detect_expiring_contracts(self: Task) -> None:
    """Stub (issue #4): Beat dispara esta tarea diariamente, 01:30 UTC.

    Logica real (notificar contratos por vencer) llega con el issue #19 —
    sdd_04 §1.3.
    """
    logger.info(
        "detect_expiring_contracts stub — logica real llega con el issue #19",
        extra={"attempt": self.request.retries + 1, "service": "notification_worker"},
    )
