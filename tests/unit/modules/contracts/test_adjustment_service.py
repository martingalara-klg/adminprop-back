"""Tests unitarios de `adminprop.modules.contracts.adjustment_service` (issue #18).

SDD: spec_module_03_contratos.md §RF-04. Cubre `_add_months` -- la
aritmetica pura de fechas usada por `ContractAdjustmentService.detect_due_adjustments`
para calcular el proximo `due_period` (RN-C03).
"""

from __future__ import annotations

from datetime import date

from adminprop.modules.contracts.adjustment_service import _add_months


def test_add_months_within_same_year():
    assert _add_months(date(2026, 1, 1), 3) == date(2026, 4, 1)


def test_add_months_normalizes_to_day_one_regardless_of_anchor_day():
    """`due_period` es siempre dia 1 del mes (CHECK `date_trunc` de la
    migracion #16) -- el ancla puede tener cualquier dia (ej: `start_date`
    de un contrato firmado el 15)."""
    assert _add_months(date(2026, 1, 15), 1) == date(2026, 2, 1)


def test_add_months_rolls_over_to_next_year():
    assert _add_months(date(2026, 11, 1), 3) == date(2027, 2, 1)


def test_add_months_rolls_over_multiple_years():
    assert _add_months(date(2026, 6, 1), 24) == date(2028, 6, 1)


def test_add_months_zero_returns_same_month():
    assert _add_months(date(2026, 5, 1), 0) == date(2026, 5, 1)
