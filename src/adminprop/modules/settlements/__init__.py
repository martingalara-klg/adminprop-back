"""Settlements module -- liquidaciones a propietarios (issue #29).

SDD: docs/sdd/features/spec_module_05_liquidaciones.md §RF-01/RF-02.
"""

from adminprop.modules.settlements.router import router

__all__ = ["router"]
