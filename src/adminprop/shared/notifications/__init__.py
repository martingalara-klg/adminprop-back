"""NotificationService transversal (issue #11).

SDD: infrastructure/spec_notificaciones.md RF-01/RF-03/RF-04.

Vive en `shared/` (no en `modules/`) por el mismo motivo que
`shared/audit/`: es infraestructura transversal consumida por los
módulos de negocio (ajustes, vencimientos, mantenimiento -- fases 4-6),
no un módulo con sus propios endpoints todavía (el panel in-app llega
con el issue #31).
"""

from adminprop.shared.notifications.service import emit, enqueue_pending_emails

__all__ = ["emit", "enqueue_pending_emails"]
