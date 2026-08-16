"""Hook extensible de generacion del rent_period del mes en curso (issue #17).

SDD: spec_module_03_contratos.md §RF-03 ("`draft -> active`... genera el
rent_period del mes en curso si la fecha de inicio ya paso y aun no
existe"). La tabla `rent_periods` NO existe todavia (migracion prevista
para el issue #20, Modulo 4 Cobranzas) -- este hook deja el punto de
extension declarado y llamado desde `service.activate`, mismo criterio
que `modules/properties/repository.py.PropertyRepository.has_active_dependencies`
dejo para el contrato activo antes de que este modulo existiera.

Cuando `rent_periods` exista: reemplazar el cuerpo de
`maybe_generate_current_month_rent_period` por el INSERT real
(`ON CONFLICT DO NOTHING` sobre `(contract_id, period)` para idempotencia,
sdd_04 §1.3) -- la firma ya queda lista para ese reemplazo sin tocar el
caller (`service.py.ContractService.activate`).
"""

from __future__ import annotations

from datetime import date
from uuid import UUID


async def maybe_generate_current_month_rent_period(
    *,
    contract_id: UUID,
    organization_id: UUID,
    start_date: date,
    today: date,
) -> None:
    """No-op hoy: `rent_periods` no existe (issue #20). Documentado en vez
    de omitido para que el llamador (`activate`) exprese la intencion del
    RF-03 aunque el efecto todavia no sea observable."""
    return
