"""Calculo deterministico de la serie mensual de valores locativos (issue #106).

SDD: docs/sdd/features/spec_module_03_contratos.md RF-06 (RN-09) +
core/sdd_03_api_contracts.md §8 v1.12 "GET /contracts/:id". Funcion pura
-- sin IO, sin `organization_id` (el caller ya resolvio los datos del
tenant) -- para poder testearla exhaustivamente sin DB
(docs/skills/testing.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class AppliedAdjustment:
    """Un ajuste `applied` (incluye el sintetico "Carga inicial" del
    issue #100/RN-C06) -- solo los dos campos que el calculo necesita."""

    due_period: date
    new_amount: Decimal


@dataclass(frozen=True)
class MonthlyAmountRow:
    """Un item de `monthly_amounts[]` -- `period` es el dia 1 del mes
    calendario (mismo criterio que `due_period` de `ContractAdjustment`)."""

    period: date
    amount: Decimal


def _month_start(value: date) -> date:
    return date(value.year, value.month, 1)


def _next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def compute_monthly_amounts(
    *,
    status: str,
    start_date: date,
    end_date: date,
    initial_amount: Decimal,
    applied_adjustments: list[AppliedAdjustment],
    today: date,
    terminated_at: date | None,
) -> list[MonthlyAmountRow]:
    """RF-06: serie mensual desde `start_date` hasta el mes de corte,
    orden DESCENDENTE (mes mas reciente primero).

    Mes de corte (RN-09):
    - `draft`/`active`: el mes actual (`today`) -- el contrato sigue
      vigente, `end_date` todavia no lo acota.
    - `expired`: `end_date` -- vencimiento natural, sin ambiguedad.
    - `terminated`: la fecha de terminacion efectiva (`terminated_at`,
      resuelta por el caller desde el evento `contract.terminated` de
      `audit_logs` -- `contracts` no tiene columna propia para esto).
      Si no existiera (defensivo -- el service siempre la audita en la
      MISMA transaccion que el cambio de estado), el fallback es
      `end_date`.

    Monto de cada mes (RN-09): `initial_amount` hasta el primer ajuste
    `applied` cuyo `due_period <= mes`; a partir de ahi, el `new_amount`
    del ULTIMO ajuste `applied` cuyo `due_period <= mes` (incluye los
    ajustes sinteticos "Carga inicial" del issue #100). Solo `applied`
    cuenta -- el caller ya filtro los `pending`. Contratos USD sin
    ajustes periodicos (RN-03/RN-C02): serie plana en `initial_amount`,
    salvo que tengan la carga inicial (RN-08/RN-C06, aplica a ARS y USD
    por igual) -- el algoritmo no distingue moneda, solo mira los
    ajustes `applied` que efectivamente existan.
    """
    start_period = _month_start(start_date)

    if status == "terminated":
        effective_end = terminated_at if terminated_at is not None else end_date
    else:
        effective_end = end_date

    last_period = min(_month_start(today), _month_start(effective_end))

    if last_period < start_period:
        return []

    sorted_adjustments = sorted(applied_adjustments, key=lambda adjustment: adjustment.due_period)

    periods: list[date] = []
    cursor = start_period
    while cursor <= last_period:
        periods.append(cursor)
        cursor = _next_month(cursor)

    rows: list[MonthlyAmountRow] = []
    for period in periods:
        amount = initial_amount
        for adjustment in sorted_adjustments:
            if adjustment.due_period <= period:
                amount = adjustment.new_amount
            else:
                break
        rows.append(MonthlyAmountRow(period=period, amount=amount))

    rows.reverse()
    return rows
