"""Modulo personas -- propietarios (landlords) e inquilinos (renters), issue #13.

SDD: docs/sdd/features/spec_module_02_personas.md RF-01..RF-03 +
core/sdd_03_api_contracts.md §5 "Propietarios" + §6 "Inquilinos".

Alcance de este issue: CA-02-01, 02, 03, 04, 06, 07. `GET /renters/:id/debt`
(CA-02-05, depende de cobranzas) se agrego en el issue #23.
`GET /landlords/:id/settlements` (historial de liquidaciones, sdd_03 §5) se
agrego en el issue #30, una vez que el modulo de Liquidaciones existe --
CA-05-07 ("descargables desde ... la ficha del propietario").
"""

from adminprop.modules.people.router import landlords_router, renters_router

__all__ = ["landlords_router", "renters_router"]
