"""Tests unitarios de `adminprop.modules.payments.service.PaymentService`
(issue #22) -- formulas puras de RN-P02/P03 (mora sugerida), sin DB.

SDD: spec_module_04_cobranzas.md §RF-04.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from adminprop.modules.payments.service import PaymentService


def test_rn_p02_days_late_is_zero_within_grace_day_inclusive():
    """RN-P02: "en termino hasta el dia de gracia inclusive"."""
    period = date(2026, 6, 1)
    assert PaymentService._days_late(period, date(2026, 6, 10), grace_day=10) == 0


def test_rn_p02_day_after_grace_day_is_one_day_late():
    """RN-P02: "dia 11 = 1 dia de mora"."""
    period = date(2026, 6, 1)
    assert PaymentService._days_late(period, date(2026, 6, 11), grace_day=10) == 1


def test_ca_04_05_day_15_with_grace_10_is_5_days_late():
    """CA-04-05: "pagando el dia 15 con dia de gracia 10... 5 dias de mora"."""
    period = date(2026, 6, 1)
    assert PaymentService._days_late(period, date(2026, 6, 15), grace_day=10) == 5


def test_days_late_never_negative_when_paid_before_grace_day():
    period = date(2026, 6, 1)
    assert PaymentService._days_late(period, date(2026, 6, 3), grace_day=10) == 0


def test_rn_p03_suggested_interest_is_zero_when_not_late():
    assert PaymentService._suggested_interest(Decimal("1000.00"), Decimal("1.0"), 0) == Decimal(
        "0.00"
    )


def test_rn_p03_suggested_interest_is_zero_when_balance_is_zero():
    assert PaymentService._suggested_interest(Decimal("0.00"), Decimal("1.0"), 5) == Decimal("0.00")


def test_ca_04_05_suggested_interest_over_balance_times_pct_times_days():
    """RN-P03: "interes sugerido = saldo impago x % de mora diaria del
    contrato x dias de mora"."""
    suggested = PaymentService._suggested_interest(Decimal("1000.00"), Decimal("1.0"), 5)
    assert suggested == Decimal("50.00")


def test_ca_04_04_suggested_interest_only_over_remaining_balance():
    """CA-04-04: "el interes de un pago posterior se calcula solo sobre
    el saldo restante" -- misma tasa/dias, saldo menor -> interes menor."""
    full_balance_interest = PaymentService._suggested_interest(
        Decimal("1000.00"), Decimal("1.0"), 5
    )
    partial_balance_interest = PaymentService._suggested_interest(
        Decimal("600.00"), Decimal("1.0"), 5
    )
    assert partial_balance_interest < full_balance_interest
    assert partial_balance_interest == Decimal("30.00")
