"""Tests unitarios de `adminprop.modules.charges` -- parseo de `period` y
validacion "no futuro" (RF-05 §Validaciones), sin DB.

SDD: docs/sdd/features/spec_module_05_liquidaciones.md §RF-05
§Validaciones -- "period: mes valido no futuro".
"""

from __future__ import annotations

from datetime import date

import pytest

from adminprop.modules.charges.schemas import parse_period
from adminprop.modules.charges.service import _period_not_future
from adminprop.shared.errors.codes import ValidationError


def test_parse_period_converts_yyyy_mm_to_first_of_month():
    assert parse_period("2026-06") == date(2026, 6, 1)


def test_parse_period_rejects_invalid_format():
    with pytest.raises(ValidationError) as exc_info:
        parse_period("06-2026")
    assert exc_info.value.error_code == "VALIDATION_ERROR"
    assert exc_info.value.field == "period"


def test_parse_period_rejects_full_date():
    with pytest.raises(ValidationError):
        parse_period("2026-06-15")


def test_period_not_future_is_true_for_current_month():
    today = date(2026, 6, 15)
    assert _period_not_future(date(2026, 6, 1), today=today) is True


def test_period_not_future_is_true_for_past_month():
    today = date(2026, 6, 15)
    assert _period_not_future(date(2026, 5, 1), today=today) is True


def test_period_not_future_is_false_for_next_month():
    today = date(2026, 6, 15)
    assert _period_not_future(date(2026, 7, 1), today=today) is False
