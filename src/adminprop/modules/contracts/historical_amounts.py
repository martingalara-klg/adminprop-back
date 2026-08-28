"""Calculo deterministico de la cadena de tramos historicos del alta de
contrato en curso v2 (issue #107, RN-C06 -- supersede parcialmente el
`current_amount` unico del issue #100 para contratos CON
`adjustment_frequency_months` configurado).

Funcion pura -- sin IO, sin `organization_id` -- mismo criterio que
`monthly_amounts.py` (issue #106): testeable exhaustivamente sin DB. El
caller (`service.py`) resuelve `today` y traduce los resultados a
excepciones de dominio (`ValidationError`).

Modelo de tramos: tramo 0 = [start_date, start_date + freq meses), tramo
1 = [start_date + freq meses, start_date + 2*freq meses), ... El tramo
"transcurrido" mas reciente es el que contiene el mes actual (inclusive).
`historical_amounts[i]` es el monto vigente durante el tramo `i` --
`historical_amounts[0]` es el monto original (debe coincidir con
`initial_amount`, validado por el caller) y cada elemento siguiente es un
aumento ya ocurrido por fuera del sistema.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


def _month_start(value: date) -> date:
    return date(value.year, value.month, 1)


def _add_months(anchor: date, months: int) -> date:
    """Mismo criterio que `adjustment_service.py._add_months`: normaliza
    siempre al dia 1 del mes resultante (`due_period` es siempre dia 1,
    CHECK `date_trunc('month', ...)` de la migracion #16)."""
    zero_based_month = anchor.month - 1 + months
    year = anchor.year + zero_based_month // 12
    month = zero_based_month % 12 + 1
    return date(year, month, 1)


def expected_tramo_count(
    *, start_date: date, adjustment_frequency_months: int, today: date
) -> int:
    """RN-C06 v2: cantidad de tramos transcurridos desde `start_date`
    (inclusive el que contiene el mes actual). Ej: `start_date` hace 10
    meses, `adjustment_frequency_months=4` -> tramo 0 (meses 1-4), tramo 1
    (meses 5-8), tramo 2 (mes 9-hoy) -> 3 tramos, `expected_tramo_count`
    devuelve 3."""
    start_period = _month_start(start_date)
    today_period = _month_start(today)
    months_elapsed = (today_period.year - start_period.year) * 12 + (
        today_period.month - start_period.month
    )
    # Defensivo -- `start_date` futuro no deberia llegar aca (otras
    # validaciones de `ContractCreate`/`service.py` ya lo acotan en los
    # flujos existentes), pero un tramo 0 sigue siendo la respuesta minima
    # sensata en vez de un indice negativo.
    if months_elapsed < 0:
        return 1
    current_tramo_index = months_elapsed // adjustment_frequency_months
    return current_tramo_index + 1


@dataclass(frozen=True)
class TramoRange:
    """Rango `[start, end)` de un tramo -- usado por el service para
    armar el mensaje de `400 VALIDATION_ERROR` cuando la cantidad de
    `historical_amounts[]` no coincide con `expected_tramo_count`."""

    index: int
    start: date
    end: date


def tramo_ranges(
    *, start_date: date, adjustment_frequency_months: int, count: int
) -> list[TramoRange]:
    start_period = _month_start(start_date)
    ranges: list[TramoRange] = []
    for i in range(count):
        tramo_start = _add_months(start_period, i * adjustment_frequency_months)
        tramo_end = _add_months(start_period, (i + 1) * adjustment_frequency_months)
        ranges.append(TramoRange(index=i, start=tramo_start, end=tramo_end))
    return ranges


@dataclass(frozen=True)
class SyntheticAdjustment:
    """Un ajuste sintetico "Carga inicial" a crear -- `due_period` es el
    inicio del tramo (donde empieza a regir `new_amount`), encadenado con
    el tramo anterior via `previous_amount`."""

    due_period: date
    previous_amount: Decimal
    new_amount: Decimal


def build_synthetic_chain(
    *,
    start_date: date,
    adjustment_frequency_months: int,
    historical_amounts: list[Decimal],
) -> list[SyntheticAdjustment]:
    """Ya validada la cantidad exacta (`expected_tramo_count`) y que
    `historical_amounts[0] == initial_amount` (el caller lo valido antes)
    -- arma la cadena de ajustes sinteticos "Carga inicial", uno por cada
    tramo A PARTIR DEL SEGUNDO (el tramo 0 es `initial_amount`, no genera
    ajuste). `due_period` de cada uno es el inicio de su tramo."""
    start_period = _month_start(start_date)
    chain: list[SyntheticAdjustment] = []
    for i in range(1, len(historical_amounts)):
        due_period = _add_months(start_period, i * adjustment_frequency_months)
        chain.append(
            SyntheticAdjustment(
                due_period=due_period,
                previous_amount=historical_amounts[i - 1],
                new_amount=historical_amounts[i],
            )
        )
    return chain
