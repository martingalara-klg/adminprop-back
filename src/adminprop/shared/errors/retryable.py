"""Clasificacion Retryable/NonRetryable para tareas Celery (issue #4).

SDD: core/sdd_04_nonfunctional.md §1.3 ("RetryableError (5xx, timeout,
rate limit del proveedor) reintenta; NonRetryableError (4xx, datos
invalidos) marca fallo inmediato y notifica").
Skill: docs/skills/async-worker.md ("Categorizacion reintentable vs no
reintentable").

Base compartida por todos los workers (no solo notification_worker):
`documents_worker` (issue #29/#30) reutiliza las mismas clases base para
sus propios errores especificos.
"""


class RetryableError(Exception):
    """Base: el proximo intento puede tener exito (5xx, timeout, 429)."""


class NonRetryableError(Exception):
    """Base: reintentar no cambia el resultado (4xx estructural, regla de negocio)."""


class RetryableNotificationError(RetryableError):
    """Resend transient: 429, 5xx, timeouts (spec_notificaciones.md §Apendice)."""


class NonRetryableNotificationError(NonRetryableError):
    """Email invalido (400) o destinatario eliminado (404) en Resend."""
