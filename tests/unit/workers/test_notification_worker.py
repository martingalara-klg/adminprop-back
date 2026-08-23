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
    _build_email_content,
    detect_due_adjustments,
    detect_expiring_contracts,
    generate_rent_periods,
    retry_countdown_seconds,
    send_notification_email,
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


def test_ca_04_01_generate_rent_periods_runs_async_body_once(monkeypatch):
    """CA-04-01 (issue #21): la tarea Celery delega en
    `_generate_rent_periods_async` via `asyncio.run(...)` -- mismo
    criterio que `test_ca_03_04_detect_due_adjustments_runs_async_body_once`
    (el body real toca Postgres, no corresponde a un test unitario). La
    cobertura del cuerpo async real (generacion multi-org, idempotencia,
    RN-P01) vive en tests/integration/contracts/test_rent_period_hook.py
    y tests/integration/workers/test_generate_rent_periods.py."""
    mock_async = AsyncMock(return_value=None)
    monkeypatch.setattr(notification_worker, "_generate_rent_periods_async", mock_async)

    result = generate_rent_periods.apply().get()

    assert result is None
    assert mock_async.call_count == 1


def test_ca_03_04_detect_due_adjustments_runs_async_body_once(monkeypatch):
    """CA-03-04 (issue #18): la tarea Celery delega en
    `_detect_due_adjustments_async` via `asyncio.run(...)` -- mismo
    criterio que los tests de arriba mockean `send_email`/
    `_send_notification_email_async` para no tocar Postgres desde un test
    unitario. La cobertura del cuerpo async real (deteccion multi-org,
    creacion del ajuste `pending`, notificacion) vive en
    tests/integration/contracts/test_adjustments.py y
    tests/integration/workers/test_detect_due_adjustments.py."""
    mock_async = AsyncMock(return_value=None)
    monkeypatch.setattr(notification_worker, "_detect_due_adjustments_async", mock_async)

    result = detect_due_adjustments.apply().get()

    assert result is None
    assert mock_async.call_count == 1


def test_ca_03_07_detect_expiring_contracts_runs_async_body_once(monkeypatch):
    """CA-03-07 (issue #19): la tarea Celery delega en
    `_detect_expiring_contracts_async` via `asyncio.run(...)` -- mismo
    criterio que `test_ca_03_04_detect_due_adjustments_runs_async_body_once`
    (el body real toca Postgres, no corresponde a un test unitario). La
    cobertura del cuerpo async real (deteccion multi-org, transicion
    active->expired, notificacion) vive en
    tests/integration/workers/test_detect_expiring_contracts.py."""
    mock_async = AsyncMock(return_value=None)
    monkeypatch.setattr(notification_worker, "_detect_expiring_contracts_async", mock_async)

    result = detect_expiring_contracts.apply().get()

    assert result is None
    assert mock_async.call_count == 1


# ─── Issue #11 — send_notification_email (outbox) ──────────────────────────
#
# Mismo motivo que la nota de arriba: `_send_notification_email_async` hace
# `asyncio.run(...)` via el wrapper sincronico -- estos tests mockean
# `notification_worker._send_notification_email_async` directamente (no
# tocan Postgres) para probar SOLO la politica de reintentos de la tarea,
# igual que los tests de `send_transactional_email` mockean `send_email`.
# La cobertura del cuerpo async real (lock, envio, mark_email_sent) vive en
# tests/integration/workers/test_notification_worker_outbox.py.


def test_ca_nt_03_send_notification_email_succeeds_on_first_try(monkeypatch):
    mock_async = AsyncMock(return_value=None)
    monkeypatch.setattr(notification_worker, "_send_notification_email_async", mock_async)

    result = send_notification_email.apply(
        args=[
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
            "req-1",
        ]
    ).get()

    assert result is None
    assert mock_async.call_count == 1


def test_ca_nt_03_send_notification_email_retries_then_succeeds(monkeypatch):
    """CA-NT-03: reintenta con el mismo backoff 30/90/270s que CA-4-02."""
    mock_async = AsyncMock(
        side_effect=[RetryableNotificationError("502"), RetryableNotificationError("503"), None]
    )
    monkeypatch.setattr(notification_worker, "_send_notification_email_async", mock_async)

    result = send_notification_email.apply(
        args=[
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
            "req-2",
        ]
    ).get()

    assert result is None
    assert mock_async.call_count == 3


def test_ca_nt_03_send_notification_email_dead_letters_after_max_retries(monkeypatch, caplog):
    """CA-NT-03: agotados los reintentos, la tarea NO propaga la excepcion
    (dead-letter) -- `email_sent_at` queda NULL (nunca se llamo
    `mark_email_sent`, porque el mock reemplaza toda la corrutina) y el
    fallo queda logueado."""
    mock_async = AsyncMock(side_effect=RetryableNotificationError("still down"))
    monkeypatch.setattr(notification_worker, "_send_notification_email_async", mock_async)

    with caplog.at_level("ERROR"):
        result = send_notification_email.apply(
            args=[
                "11111111-1111-1111-1111-111111111111",
                "22222222-2222-2222-2222-222222222222",
                "req-3",
            ]
        ).get()

    assert result is None
    assert mock_async.call_count == MAX_RETRIES + 1
    dead_letter_records = [r for r in caplog.records if "dead-letter" in r.getMessage()]
    assert len(dead_letter_records) == 1
    # request_id pasado explicito a `extra=` en el log call (verificado
    # via los kwargs del record -- `RequestIdFilter`, agregado solo en el
    # handler configurado por `setup_logging()`, no corre sobre
    # `caplog`, asi que aca se ve el valor tal como lo paso el codigo).
    assert dead_letter_records[0].notification_id == "11111111-1111-1111-1111-111111111111"


def test_ca_nt_03_send_notification_email_non_retryable_gives_up_immediately(monkeypatch):
    mock_async = AsyncMock(side_effect=NonRetryableNotificationError("invalid email"))
    monkeypatch.setattr(notification_worker, "_send_notification_email_async", mock_async)

    result = send_notification_email.apply(
        args=[
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
            "req-4",
        ]
    ).get()

    assert result is None
    assert mock_async.call_count == 1


def test_build_email_content_work_order_created_includes_link_when_payload_has_id():
    subject, html, text = _build_email_content("work_order_created", {"work_order_id": "abc-123"})
    assert subject == "Nuevo pedido de mantenimiento"
    assert "abc-123" in html
    assert "abc-123" in text


def test_build_email_content_falls_back_to_no_link_when_payload_key_missing():
    """Los emisores reales (issues #18/#19/#26) todavia no existen -- un
    payload sin la clave esperada por el template no debe romper el
    envio, solo omitir el link."""
    subject, html, text = _build_email_content("work_order_created", {})
    assert subject == "Nuevo pedido de mantenimiento"
    assert "href" not in html
    assert "Ver en AdminProp" not in text


def test_build_email_content_unknown_event_type_uses_generic_copy():
    subject, html, _text = _build_email_content("not_a_real_event", {})
    assert subject == "Notificación de AdminProp"
    assert "Ver en AdminProp" not in html
