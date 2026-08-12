"""Issue #4 — notification_worker: retry 30/90/270s + jitter, Beat stubs.

SDD: core/sdd_04_nonfunctional.md §1.3, infrastructure/spec_notificaciones.md §Email.
Skill: docs/skills/async-worker.md.
Implements: CA-4-02, CA-4-04.

Las tareas Celery de este modulo llaman `asyncio.run(...)` internamente
(async-worker.md), por lo que estos tests son sincronicos (`def`, no
`async def`): invocar un `asyncio.run()` desde un test ya corriendo
dentro del loop de pytest-asyncio (`asyncio_mode = auto`) fallaria con
"cannot be called from a running event loop". `.apply()` de Celery
ejecuta la tarea eagerly (sincronico) sin necesidad de tocar
`task_always_eager` global.
"""

from unittest.mock import AsyncMock

from adminprop.shared.errors.retryable import (
    NonRetryableNotificationError,
    RetryableNotificationError,
)
from adminprop.workers import notification_worker
from adminprop.workers.notification_worker import (
    MAX_RETRIES,
    RETRY_DELAYS_SECONDS,
    detect_due_adjustments,
    detect_expiring_contracts,
    generate_rent_periods,
    retry_countdown_seconds,
    send_transactional_email,
)


def test_ca_4_02_retry_countdown_seconds_matches_30_90_270_with_jitter():
    """CA-4-02: los 3 delays base son 30s -> 90s -> 270s, con jitter >= 0
    y <= 20% del delay base (nunca negativo, nunca por debajo del piso)."""
    for retries, base in enumerate(RETRY_DELAYS_SECONDS):
        delay = retry_countdown_seconds(retries)
        assert base <= delay <= base * 1.2


def test_retry_countdown_seconds_caps_at_last_delay_for_out_of_range_retries():
    """Un `retries` mayor al numero de delays declarados usa 270s (el
    ultimo) como piso de seguridad — nunca crashea por IndexError."""
    delay = retry_countdown_seconds(99)
    assert 270 <= delay <= 270 * 1.2


def test_ca_4_02_send_transactional_email_succeeds_on_first_try(monkeypatch):
    """CA-4-02: si Resend responde OK al primer intento, no hay reintento."""
    mock_send = AsyncMock(return_value="msg-ok")
    monkeypatch.setattr(notification_worker, "send_email", mock_send)

    result = send_transactional_email.apply(
        kwargs={
            "to": ["a@b.com"],
            "subject": "s",
            "html": "h",
            "organization_name": "Acme",
            "request_id": "req-1",
        }
    ).get()

    assert result == "msg-ok"
    assert mock_send.call_count == 1


def test_ca_4_02_send_transactional_email_retries_then_succeeds(monkeypatch):
    """CA-4-02: 2 fallos retryable seguidos de un exito -> reintenta y
    termina con el message_id (Celery `.apply()` reejecuta eager con
    `retries` incrementado en cada `self.retry()`)."""
    mock_send = AsyncMock(
        side_effect=[
            RetryableNotificationError("502"),
            RetryableNotificationError("503"),
            "msg-after-retries",
        ]
    )
    monkeypatch.setattr(notification_worker, "send_email", mock_send)

    result = send_transactional_email.apply(
        kwargs={
            "to": ["a@b.com"],
            "subject": "s",
            "html": "h",
            "organization_name": "Acme",
            "request_id": "req-2",
        }
    ).get()

    assert result == "msg-after-retries"
    assert mock_send.call_count == 3


def test_ca_4_02_send_transactional_email_dead_letters_after_max_retries(monkeypatch):
    """CA-4-02: agotados los `max_retries` (3) con error siempre retryable,
    la tarea deja de reintentar (dead-letter) y retorna None en vez de
    propagar la excepcion o reintentar indefinidamente."""
    mock_send = AsyncMock(side_effect=RetryableNotificationError("still down"))
    monkeypatch.setattr(notification_worker, "send_email", mock_send)

    result = send_transactional_email.apply(
        kwargs={
            "to": ["a@b.com"],
            "subject": "s",
            "html": "h",
            "organization_name": "Acme",
            "request_id": "req-3",
        }
    ).get()

    assert result is None
    # 1 intento original + MAX_RETRIES reintentos = MAX_RETRIES + 1 llamadas.
    assert mock_send.call_count == MAX_RETRIES + 1


def test_ca_4_02_send_transactional_email_non_retryable_gives_up_immediately(monkeypatch):
    """CA-4-02: un error no-retryable (ej: email invalido) no reintenta —
    falla en el primer intento y retorna None."""
    mock_send = AsyncMock(side_effect=NonRetryableNotificationError("invalid email"))
    monkeypatch.setattr(notification_worker, "send_email", mock_send)

    result = send_transactional_email.apply(
        kwargs={
            "to": ["not-an-email"],
            "subject": "s",
            "html": "h",
            "organization_name": "Acme",
            "request_id": "req-4",
        }
    ).get()

    assert result is None
    assert mock_send.call_count == 1


def test_ca_4_04_generate_rent_periods_stub_runs_without_error():
    """CA-4-04: el stub de Beat corre sin excepcion (issue #21 agrega la logica)."""
    assert generate_rent_periods.apply().get() is None


def test_ca_4_04_detect_due_adjustments_stub_runs_without_error():
    """CA-4-04: el stub de Beat corre sin excepcion (issue #18 agrega la logica)."""
    assert detect_due_adjustments.apply().get() is None


def test_ca_4_04_detect_expiring_contracts_stub_runs_without_error():
    """CA-4-04: el stub de Beat corre sin excepcion (issue #19 agrega la logica)."""
    assert detect_expiring_contracts.apply().get() is None
