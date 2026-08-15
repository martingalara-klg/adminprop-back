"""Property module -- inventario de inmuebles + cuentas de servicio (issue #15).

SDD: docs/sdd/features/spec_module_01_propiedades.md RF-01..RF-03.
"""

from adminprop.modules.properties.router import (
    properties_router,
    service_accounts_router,
)

__all__ = ["properties_router", "service_accounts_router"]
