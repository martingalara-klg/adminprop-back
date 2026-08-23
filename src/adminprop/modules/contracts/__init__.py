"""Contracts module -- ciclo de vida de contratos de locacion + ajustes
por indice (issues #17 y #18).

SDD: docs/sdd/features/spec_module_03_contratos.md RF-01..RF-04.
"""

from adminprop.modules.contracts.router import adjustments_router, router

__all__ = ["adjustments_router", "router"]
