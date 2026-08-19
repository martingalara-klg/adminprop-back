"""notification_worker — email transaccional (Resend) + outbox + Beat stubs.

SDD: core/sdd_04_nonfunctional.md §1.3, infrastructure/spec_notificaciones.md §Email/§RF-04.
Skill: docs/skills/async-worker.md, docs/skills/external-integrations.md.
Implements: CA-4-02 (retry 30/90/270s + jitter, clasificacion Retryable/NonRetryable),
            CA-NT-03 (issue #11: Resend caido -- el aviso in-app existe, el email
            reintenta con el mismo backoff y agotados los reintentos queda
            registrado el fallo con request_id).

`send_transactional_email` es la tarea generica reutilizable (issue #4).
`send_notification_email` (issue #11) es el consumidor real del patron
outbox: `shared.notifications.service.emit()` crea las filas in-app en la
transaccion de negocio del caller, y DESPUES del commit,
`enqueue_pending_emails()` encola esta tarea por cada notificacion creada
-- toma la fila con `email_sent_at IS NULL` (`FOR UPDATE SKIP LOCKED`,
idempotente ante corridas concurrentes), envia el email y marca
`email_sent_at`. Reusa la misma politica de reintentos (30/90/270s +
jitter) que `send_transactional_email`, pero no delega en esa tarea
porque necesita actualizar la fila de `notifications` despues del envio.

Las 3 tareas de Celery Beat (`generate_rent_periods`, `detect_due_adjustments`,
`detect_expiring_contracts`) partieron como stubs (CA-4-04) que solo
logueaban inicio/fin para validar que Beat las dispara end-to-end. Las
tres ya tienen su logica de negocio real: `detect_due_adjustments` (issue
#18), `detect_expiring_contracts` (issue #19) y `generate_rent_periods`
(issue #21, spec_module_04_cobranzas.md §RF-01).
"""

import asyncio
import logging
import random
import uuid
from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from celery import Task

from adminprop.config import get_settings
from adminprop.db.session import get_session_factory, set_tenant_context, tenant_scoped_session
from adminprop.modules.contracts.adjustment_repository import ContractAdjustmentRepository
from adminprop.modules.contracts.adjustment_service import ContractAdjustmentService
from adminprop.modules.contracts.repository import ContractRepository
from adminprop.modules.contracts.service import ContractService
from adminprop.modules.payments.repository import RentPeriodRepository
from adminprop.modules.payments.service import RentPeriodService
from adminprop.modules.properties.repository import PropertyRepository
from adminprop.shared.email.sender import send_email
from adminprop.shared.errors.retryable import (
    NonRetryableNotificationError,
    RetryableNotificationError,
)
from adminprop.shared.notifications.repository import NotificationRepository
from adminprop.shared.notifications.service import enqueue_pending_emails
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


# ─── Outbox de notificaciones (issue #11) ──────────────────────────────────

# spec_notificaciones.md §Email: "asunto corto por evento + cuerpo con el
# dato esencial y link directo. Sin adjuntos en MVP". Los emisores reales
# de negocio (ajustes, vencimientos, mantenimiento) llegan en las fases
# 4-6 (issues #18, #19, #26) y son quienes definen la forma final de
# `payload` -- estos templates son deliberadamente simples (placeholder
# documentado como decision de implementacion de este issue) y arman el
# link con la primera clave de `payload` que matchee `link_path`,
# degradando a "sin link" si la clave todavia no existe.
_EVENT_COPY: dict[str, dict[str, str]] = {
    "adjustment_pending": {
        "subject": "Ajuste de alquiler pendiente",
        "body": "Hay un ajuste de alquiler pendiente de aplicar en un contrato de tu organización.",
        "link_path": "/contracts/{contract_id}",
    },
    "contract_expiring": {
        "subject": "Contrato próximo a vencer",
        "body": "Un contrato de tu organización está próximo a vencer.",
        "link_path": "/contracts/{contract_id}",
    },
    "work_order_created": {
        "subject": "Nuevo pedido de mantenimiento",
        "body": "Se creó un nuevo pedido de mantenimiento asignado a tu rol.",
        "link_path": "/maintenance/work-orders/{work_order_id}",
    },
    "quote_submitted": {
        "subject": "Cotización recibida",
        "body": "Se recibió una cotización para un pedido de mantenimiento.",
        "link_path": "/maintenance/work-orders/{work_order_id}",
    },
    "work_order_closed": {
        "subject": "Trabajo de mantenimiento finalizado",
        "body": "Se cerró un pedido de mantenimiento.",
        "link_path": "/maintenance/work-orders/{work_order_id}",
    },
}


def _build_email_content(event_type: str, payload: dict) -> tuple[str, str, str]:
    """Arma `(subject, html, text)` del email para `event_type`.

    `event_type` ya fue validado por `shared.notifications.service.emit()`
    contra el mismo catalogo de 5 eventos (CHECK de la migracion) -- el
    `.get()` con default es defensivo, no deberia activarse en runtime.
    """
    copy = _EVENT_COPY.get(
        event_type,
        {
            "subject": "Notificación de AdminProp",
            "body": "Tenés una notificación nueva.",
            "link_path": "",
        },
    )

    link = ""
    if copy["link_path"]:
        try:
            link = get_settings().frontend_base_url + copy["link_path"].format(**payload)
        except KeyError:
            # El payload todavia no trae la clave que este template
            # espera (emisor real pendiente, issues #18/#19/#26) --
            # se envia el email sin link en vez de fallar.
            link = ""

    html = f"<p>{copy['body']}</p>"
    text = copy["body"]
    if link:
        html += f'<p><a href="{link}">Ver en AdminProp</a></p>'
        text += f" Ver en AdminProp: {link}"
    return copy["subject"], html, text


class NotificationEmailOutboxTask(Task):
    """Politica de reintentos del outbox de email — mismos delays fijos
    30s/90s/270s + jitter que `NotificationTask` (sdd_04 §1.3,
    spec_notificaciones.md §Apendice). Clase separada (no se reusa
    `NotificationTask`) para que cada tarea declare su propio
    `max_retries` de forma explicita, aunque hoy el valor coincide."""

    max_retries = MAX_RETRIES


@celery_app.task(
    base=NotificationEmailOutboxTask,
    bind=True,
    name="adminprop.workers.notification_worker.send_notification_email",
)
def send_notification_email(
    self: Task, notification_id: str, organization_id: str, request_id: str
) -> None:
    """RF-01/RF-04 (spec_notificaciones.md): envía el email outbox de una
    notificación in-app ya creada por `shared.notifications.service.emit()`
    (issue #11). Implements: CA-NT-03.

    Idempotente ante corridas concurrentes: `NotificationRepository.
    lock_pending_email` usa `FOR UPDATE SKIP LOCKED` + filtra
    `email_sent_at IS NULL` -- si la fila ya fue enviada, o esta siendo
    procesada por otra corrida, retorna sin hacer nada (sin duplicar el
    envio).
    """
    retries = self.request.retries
    attempt = retries + 1
    logger.info(
        "send_notification_email start",
        extra={
            "request_id": request_id,
            "notification_id": notification_id,
            "attempt": attempt,
            "service": "notification_worker",
        },
    )
    try:
        asyncio.run(
            _send_notification_email_async(UUID(notification_id), UUID(organization_id), request_id)
        )
    except RetryableNotificationError as exc:
        logger.warning(
            "send_notification_email retryable error",
            extra={
                "request_id": request_id,
                "notification_id": notification_id,
                "attempt": attempt,
                "error": str(exc),
            },
        )
        if retries >= MAX_RETRIES:
            # Dead-letter (spec_notificaciones.md §"Apendice"): el aviso
            # in-app ya existe (CA-NT-03); `email_sent_at` queda NULL,
            # sin reintento automatico posterior. `request_id` queda en
            # el log para diagnostico (RF-04).
            logger.error(
                "send_notification_email exhausted retries, dead-letter",
                extra={
                    "request_id": request_id,
                    "notification_id": notification_id,
                    "attempts": attempt,
                    "error": str(exc),
                },
            )
            return
        raise self.retry(
            exc=exc, countdown=retry_countdown_seconds(retries), max_retries=MAX_RETRIES
        )
    except NonRetryableNotificationError as exc:
        logger.error(
            "send_notification_email non-retryable, giving up",
            extra={
                "request_id": request_id,
                "notification_id": notification_id,
                "attempt": attempt,
                "error": str(exc),
            },
        )
        return


async def _send_notification_email_async(
    notification_id: UUID, organization_id: UUID, request_id: str
) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        # RN-D01 / docs/skills/tenant-isolation.md: el worker no tiene
        # middleware que setee el tenant context -- se hace explicito
        # antes de cualquier query.
        await set_tenant_context(session, organization_id)
        repo = NotificationRepository(session)

        notification = await repo.lock_pending_email(
            notification_id=notification_id, organization_id=organization_id
        )
        if notification is None:
            # Ya enviada, o lockeada por otra corrida concurrente del
            # outbox -- idempotente, nada que hacer (`session.begin()`
            # comitea un no-op).
            return

        org_context = await repo.get_organization_email_context(organization_id)
        organization_name = org_context[0] if org_context else "AdminProp"
        owner_reply_email = org_context[1] if org_context else None

        subject, html, text = _build_email_content(notification.event_type, notification.payload)

        # `send_email` puede levantar Retryable/NonRetryableNotificationError
        # -- se propaga sin capturar: `session.begin()` hace rollback
        # automatico (no se llego a marcar `email_sent_at`) y la excepcion
        # sigue subiendo hasta `send_notification_email` para decidir el
        # reintento (CA-NT-03).
        await send_email(
            to=[notification.recipient_email],
            subject=subject,
            html=html,
            text=text,
            organization_name=organization_name,
            owner_reply_email=owner_reply_email,
            request_id=request_id,
        )

        await repo.mark_email_sent(notification_id)


# ─── Celery Beat (CA-4-04) ──────────────────────────────────────────────────
# `detect_due_adjustments` (RN-C03, issue #18), `detect_expiring_contracts`
# (issue #19) y `generate_rent_periods` (RN-P01, issue #21) ya tienen
# logica real.


@celery_app.task(bind=True, name="adminprop.workers.notification_worker.generate_rent_periods")
def generate_rent_periods(self: Task) -> None:
    """Beat dispara esta tarea el 1° de cada mes, 00:30 UTC.

    SDD: spec_module_04_cobranzas.md §RF-01. Implements: CA-04-01
    (idempotencia), CA-04-02 (RN-P01). Itera TODAS las organizaciones
    `active` (el Beat corre sin tenant) y, por cada una, setea el
    contexto y genera -- mismo patron que `_detect_due_adjustments_async`
    (issue #18) y `_detect_expiring_contracts_async` (issue #19).
    """
    request_id = str(uuid.uuid4())
    logger.info(
        "generate_rent_periods start",
        extra={
            "request_id": request_id,
            "attempt": self.request.retries + 1,
            "service": "notification_worker",
        },
    )
    asyncio.run(_generate_rent_periods_async(request_id))
    logger.info(
        "generate_rent_periods done",
        extra={"request_id": request_id, "service": "notification_worker"},
    )


async def _generate_rent_periods_async(request_id: str) -> None:
    today = datetime.now(UTC).date()
    organization_ids = await _list_active_organization_ids()

    for organization_id in organization_ids:
        async with tenant_scoped_session(organization_id) as session:
            contract_repo = ContractRepository(session)
            rent_period_repo = RentPeriodRepository(session)
            service = RentPeriodService(rent_period_repo, contract_repo)
            rent_periods_created = await service.generate_monthly(
                organization_id=organization_id, today=today
            )
            # `tenant_scoped_session` comitea al salir del bloque sin
            # excepcion (mismo patron que `_detect_due_adjustments_async`).

        logger.info(
            "generate_rent_periods organization done",
            extra={
                "request_id": request_id,
                "organization_id": str(organization_id),
                # `created` es un atributo reservado de `LogRecord`
                # (timestamp de creacion del record) -- usar ese nombre en
                # `extra` revienta `logging` con
                # "Attempt to overwrite 'created' in LogRecord".
                "rent_periods_created": rent_periods_created,
                "service": "notification_worker",
            },
        )


@celery_app.task(bind=True, name="adminprop.workers.notification_worker.detect_due_adjustments")
def detect_due_adjustments(self: Task) -> None:
    """Beat dispara esta tarea diariamente, 01:00 UTC.

    SDD: spec_module_03_contratos.md §RF-04 paso 1. Implements: CA-03-04
    (RN-C03). Itera TODAS las organizaciones `active` (el Beat corre sin
    tenant) y, por cada una, setea el contexto y detecta -- mismo patron
    que `async-worker.md` documenta para cualquier tarea que no tenga un
    `organization_id` de origen HTTP. Idempotente: el indice parcial
    unico `idx_contract_adjustments_one_pending_per_contract` (migracion
    #16) mas el chequeo previo de `ContractAdjustmentService.detect_due_adjustments`
    garantizan que re-correr la tarea el mismo dia no duplica ajustes.
    """
    request_id = str(uuid.uuid4())
    logger.info(
        "detect_due_adjustments start",
        extra={
            "request_id": request_id,
            "attempt": self.request.retries + 1,
            "service": "notification_worker",
        },
    )
    asyncio.run(_detect_due_adjustments_async(request_id))
    logger.info(
        "detect_due_adjustments done",
        extra={"request_id": request_id, "service": "notification_worker"},
    )


async def _list_active_organization_ids() -> list[UUID]:
    """`organizations` es la tabla raiz del tenant, sin RLS (migracion
    #5) -- `adminprop_app` tiene GRANT SELECT directo, no hace falta
    `set_tenant_context` para leerla."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            sa.text("SELECT id FROM organizations WHERE status = 'active' AND deleted_at IS NULL")
        )
        return [row.id for row in result]


async def _detect_due_adjustments_async(request_id: str) -> None:
    today = datetime.now(UTC).date()
    organization_ids = await _list_active_organization_ids()

    for organization_id in organization_ids:
        notification_ids: list[UUID] = []
        async with tenant_scoped_session(organization_id) as session:
            adjustment_repo = ContractAdjustmentRepository(session)
            contract_repo = ContractRepository(session)
            service = ContractAdjustmentService(adjustment_repo, contract_repo)
            notification_ids = await service.detect_due_adjustments(
                organization_id=organization_id, today=today
            )
            # `tenant_scoped_session` comitea al salir del bloque sin
            # excepcion (mismo patron que `db/session.py`).

        # RF-01 (spec_notificaciones.md): el email se encola DESPUES del
        # commit de la operacion de negocio -- patron outbox, mismo
        # criterio que `shared/notifications/service.emit` documenta.
        if notification_ids:
            enqueue_pending_emails(
                notification_ids, organization_id=organization_id, request_id=request_id
            )


@celery_app.task(bind=True, name="adminprop.workers.notification_worker.detect_expiring_contracts")
def detect_expiring_contracts(self: Task) -> None:
    """Beat dispara esta tarea diariamente, 01:30 UTC.

    SDD: spec_module_03_contratos.md §RF-03 (active -> expired automatico)
    + §RF-05 (aviso de vencimiento). Implements: CA-03-07 (RN-C05, RN-07,
    RN-D01). Itera TODAS las organizaciones `active` (el Beat corre sin
    tenant) y, por cada una, setea el contexto y detecta -- mismo patron
    que `_detect_due_adjustments_async` (issue #18) documenta en
    `async-worker.md` para cualquier tarea sin `organization_id` de origen
    HTTP.
    """
    request_id = str(uuid.uuid4())
    logger.info(
        "detect_expiring_contracts start",
        extra={
            "request_id": request_id,
            "attempt": self.request.retries + 1,
            "service": "notification_worker",
        },
    )
    asyncio.run(_detect_expiring_contracts_async(request_id))
    logger.info(
        "detect_expiring_contracts done",
        extra={"request_id": request_id, "service": "notification_worker"},
    )


async def _detect_expiring_contracts_async(request_id: str) -> None:
    today = datetime.now(UTC).date()
    organization_ids = await _list_active_organization_ids()

    for organization_id in organization_ids:
        notification_ids: list[UUID] = []
        async with tenant_scoped_session(organization_id) as session:
            contract_repo = ContractRepository(session)
            property_repo = PropertyRepository(session)
            service = ContractService(contract_repo, property_repo)
            notification_ids = await service.detect_expiring_and_expired(
                organization_id=organization_id, today=today
            )
            # `tenant_scoped_session` comitea al salir del bloque sin
            # excepcion (mismo patron que `_detect_due_adjustments_async`).

        # RF-01 (spec_notificaciones.md): el email se encola DESPUES del
        # commit de la operacion de negocio -- patron outbox.
        if notification_ids:
            enqueue_pending_emails(
                notification_ids, organization_id=organization_id, request_id=request_id
            )
