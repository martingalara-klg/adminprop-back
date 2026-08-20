"""Maintenance module -- ciclo de mantenimiento (issue #26).

SDD: docs/sdd/features/spec_module_06_mantenimiento.md.
"""

from adminprop.modules.maintenance.router import (
    attachments_router,
    property_work_orders_router,
    quotes_router,
    work_orders_router,
)

__all__ = [
    "attachments_router",
    "property_work_orders_router",
    "quotes_router",
    "work_orders_router",
]
