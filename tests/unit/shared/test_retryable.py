"""Issue #4 — jerarquia de excepciones Retryable/NonRetryable.

SDD: core/sdd_04_nonfunctional.md §1.3.
Skill: docs/skills/async-worker.md.
"""

from adminprop.shared.errors.retryable import (
    NonRetryableError,
    NonRetryableNotificationError,
    RetryableError,
    RetryableNotificationError,
)


def test_ca_4_02_retryable_notification_error_is_a_retryable_error():
    """CA-4-02: RetryableNotificationError es una subclase de RetryableError
    (la tarea Celery diferencia reintentables por herencia)."""
    assert issubclass(RetryableNotificationError, RetryableError)
    assert isinstance(RetryableNotificationError("x"), Exception)


def test_ca_4_02_non_retryable_notification_error_is_a_non_retryable_error():
    """CA-4-02: NonRetryableNotificationError es una subclase de NonRetryableError."""
    assert issubclass(NonRetryableNotificationError, NonRetryableError)


def test_ca_4_02_retryable_and_non_retryable_are_disjoint_hierarchies():
    """CA-4-02: un error retryable nunca es tambien no-retryable (evita que
    un `except RetryableError` capture por error un NonRetryableError)."""
    assert not issubclass(RetryableError, NonRetryableError)
    assert not issubclass(NonRetryableError, RetryableError)
