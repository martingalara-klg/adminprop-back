"""Panel in-app de notificaciones (issue #31).

SDD: infrastructure/spec_notificaciones.md RF-02 +
     core/sdd_03_api_contracts.md §13 "Notificaciones".
"""

from adminprop.modules.notifications.router import router

__all__ = ["router"]
