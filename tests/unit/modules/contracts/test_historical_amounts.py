"""tests/unit/modules/contracts/test_historical_amounts.py

SDD: docs/sdd/features/spec_module_03_contratos.md RF-02, RF-04 paso 6
(RN-08/RN-C06 v2, issue #107).
Implements: CA-03-09, CA-03-10, CA-03-11, CA-03-12, CA-03-13.

Tests unitarios de las funciones puras `expected_tramo_count`,
`tramo_ranges` y `build_synthetic_chain` -- sin DB, sin `organization_id`
(docs/skills/testing.md): la resolucion de datos y el armado de la
transaccion se prueban a nivel de integracion en
`tests/integration/contracts/test_contracts_crud.py`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from adminprop.modules.contracts.historical_amounts import (
    build_synthetic_chain,
    expected_tramo_count,
    tramo_ranges,
)


class TestExpectedTramoCount:
    """RN-C06 v2: cantidad de tramos transcurridos desde `start_date`
    (tramo 0 = [start, start+freq), ...) hasta el mes actual, inclusive."""

    def test_zero_elapsed_tramos_same_month_returns_one(self):
        assert (
            expected_tramo_count(
                start_date=date(2026, 8, 1),
                adjustment_frequency_months=4,
                today=date(2026, 8, 15),
            )
            == 1
        )

    def test_one_elapsed_tramo_returns_two(self):
        # freq=1: mes 1 = tramo 0, mes 2 = tramo 1 -> 2 tramos.
        assert (
            expected_tramo_count(
                start_date=date(2026, 7, 1),
                adjustment_frequency_months=1,
                today=date(2026, 8, 15),
            )
            == 2
        )

    def test_po_example_ten_months_freq_four_returns_three(self):
        # Ejemplo del PO: 10 meses corridos, ajuste cada 4 -> original
        # (meses 1-4), aumento 1 (meses 5-8), aumento 2 (mes 9-hoy).
        assert (
            expected_tramo_count(
                start_date=date(2025, 10, 1),
                adjustment_frequency_months=4,
                today=date(2026, 8, 15),
            )
            == 3
        )

    def test_exact_tramo_boundary_counts_the_new_tramo(self):
        # `today` cae EXACTO en el inicio de un tramo nuevo -- ya cuenta.
        assert (
            expected_tramo_count(
                start_date=date(2026, 1, 1),
                adjustment_frequency_months=3,
                today=date(2026, 4, 1),
            )
            == 2
        )

    def test_future_start_date_defensively_returns_one(self):
        assert (
            expected_tramo_count(
                start_date=date(2026, 12, 1),
                adjustment_frequency_months=4,
                today=date(2026, 8, 1),
            )
            == 1
        )


class TestTramoRanges:
    """Rangos `[start, end)` de cada tramo -- usados por el service para
    el mensaje de error de cantidad incorrecta."""

    def test_ranges_match_frequency_windows(self):
        ranges = tramo_ranges(
            start_date=date(2025, 10, 1), adjustment_frequency_months=4, count=3
        )

        assert [(r.index, r.start, r.end) for r in ranges] == [
            (0, date(2025, 10, 1), date(2026, 2, 1)),
            (1, date(2026, 2, 1), date(2026, 6, 1)),
            (2, date(2026, 6, 1), date(2026, 10, 1)),
        ]


class TestBuildSyntheticChain:
    """CA-03-09/10: cadena de ajustes sinteticos "Carga inicial" -- uno
    por tramo a partir del segundo, encadenados."""

    def test_single_elapsed_tramo_creates_one_link(self):
        chain = build_synthetic_chain(
            start_date=date(2026, 7, 1),
            adjustment_frequency_months=1,
            historical_amounts=[Decimal("100000.00"), Decimal("150000.00")],
        )

        assert len(chain) == 1
        assert chain[0].due_period == date(2026, 8, 1)
        assert chain[0].previous_amount == Decimal("100000.00")
        assert chain[0].new_amount == Decimal("150000.00")

    def test_po_example_creates_two_chained_links(self):
        chain = build_synthetic_chain(
            start_date=date(2025, 10, 1),
            adjustment_frequency_months=4,
            historical_amounts=[
                Decimal("100000.00"),
                Decimal("120000.00"),
                Decimal("150000.00"),
            ],
        )

        assert len(chain) == 2
        assert chain[0].due_period == date(2026, 2, 1)
        assert chain[0].previous_amount == Decimal("100000.00")
        assert chain[0].new_amount == Decimal("120000.00")
        assert chain[1].due_period == date(2026, 6, 1)
        assert chain[1].previous_amount == Decimal("120000.00")
        assert chain[1].new_amount == Decimal("150000.00")

    def test_single_element_produces_no_links(self):
        # 1 elemento = tramo 0 unicamente -- no hay "a partir del
        # segundo" (el caller no deberia llegar aca en ese caso, ver
        # CA-03-11, pero la funcion es defensiva de todos modos).
        chain = build_synthetic_chain(
            start_date=date(2026, 8, 1),
            adjustment_frequency_months=4,
            historical_amounts=[Decimal("100000.00")],
        )

        assert chain == []
