"""Modulo administracion -- usuarios, invitaciones, roles y settings de la
organizacion (issue #9).

SDD: docs/sdd/features/spec_module_07_administracion.md RF-01..RF-04.
core/sdd_03_api_contracts.md §3 "Usuarios y Roles" + §4 "Configuracion de
la Organizacion".
"""

from adminprop.modules.administracion.router import (
    organization_settings_router,
    roles_router,
    users_router,
)

__all__ = ["organization_settings_router", "roles_router", "users_router"]
