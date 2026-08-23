"""Modulo `payments` (cobranzas) -- issue #21.

SDD: docs/sdd/features/spec_module_04_cobranzas.md. Arranca con el
modelo ORM de `rent_periods` (RF-01, `RentPeriod`) y el repository/
service que soportan la generacion mensual idempotente. `Payment`
(el cobro en si -- RF-03 en adelante) es alcance de los issues #22/#23.
"""

from __future__ import annotations
