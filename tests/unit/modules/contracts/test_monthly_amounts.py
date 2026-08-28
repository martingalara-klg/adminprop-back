"""tests/unit/modules/contracts/test_monthly_amounts.py

SDD: docs/sdd/features/spec_module_03_contratos.md RF-06 (RN-09, issue #106).
Implements: CA-03-16, CA-03-17, CA-03-18, CA-03-19, CA-03-20, CA-03-21,
            CA-03-22.

Tests unitarios de la funcion pura `compute_monthly_amounts` -- sin DB,
sin `organization_id` (docs/skills/testing.md): la resolucion de datos
(ajustes `applied`, `terminated_at`) se prueba a nivel de integracion en
`tests/integration/contracts/test_monthly_amounts.py`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from adminprop.modules.contracts.monthly_amounts import (
    AppliedAdjustment,
    compute_monthly_amounts,
)


class TestCA0316FlatSeriesWithoutAdjustments:
    """CA-03-16: contrato sin ajustes -- serie plana en `initial_amount`
    desde `start_date` hasta el mes actual, orden descendente."""

    def test_ca_03_16_no_adjustments_returns_flat_series_descending(self):
        rows = compute_monthly_amounts(
            status="active",
            start_date=date(2026, 1, 1),
            end_date=date(2027, 1, 1),
            initial_amount=Decimal("100000.00"),
            applied_adjustments=[],
            today=date(2026, 4, 15),
            terminated_at=None,
        )

        periods = [row.period for row in rows]
        assert periods == [date(2026, 4, 1), date(2026, 3, 1), date(2026, 2, 1), date(2026, 1, 1)]
        assert all(row.amount == Decimal("100000.00") for row in rows)


class TestCA0317TwoAppliedAdjustments:
    """CA-03-17: contrato con 2 ajustes `applied` -- 3 tramos de monto,
    cada uno vigente desde su `due_period`."""

    def test_ca_03_17_two_applied_adjustments_produce_three_segments(self):
        rows = compute_monthly_amounts(
            status="active",
            start_date=date(2026, 1, 1),
            end_date=date(2027, 1, 1),
            initial_amount=Decimal("100000.00"),
            applied_adjustments=[
                AppliedAdjustment(due_period=date(2026, 3, 1), new_amount=Decimal("120000.00")),
                AppliedAdjustment(due_period=date(2026, 5, 1), new_amount=Decimal("140000.00")),
            ],
            today=date(2026, 6, 10),
            terminated_at=None,
        )

        by_period = {row.period: row.amount for row in rows}
        assert by_period[date(2026, 1, 1)] == Decimal("100000.00")
        assert by_period[date(2026, 2, 1)] == Decimal("100000.00")
        assert by_period[date(2026, 3, 1)] == Decimal("120000.00")
        assert by_period[date(2026, 4, 1)] == Decimal("120000.00")
        assert by_period[date(2026, 5, 1)] == Decimal("140000.00")
        assert by_period[date(2026, 6, 1)] == Decimal("140000.00")

    def test_ca_03_17_unsorted_input_adjustments_still_resolve_correctly(self):
        """El caller (`ContractAdjustmentRepository.list_applied_by_contract`)
        ya ordena ascendente por `due_period`, pero la funcion pura ordena
        de nuevo defensivamente -- no debe asumir el orden del input."""
        rows = compute_monthly_amounts(
            status="active",
            start_date=date(2026, 1, 1),
            end_date=date(2027, 1, 1),
            initial_amount=Decimal("100000.00"),
            applied_adjustments=[
                AppliedAdjustment(due_period=date(2026, 5, 1), new_amount=Decimal("140000.00")),
                AppliedAdjustment(due_period=date(2026, 3, 1), new_amount=Decimal("120000.00")),
            ],
            today=date(2026, 6, 10),
            terminated_at=None,
        )

        by_period = {row.period: row.amount for row in rows}
        assert by_period[date(2026, 4, 1)] == Decimal("120000.00")
        assert by_period[date(2026, 6, 1)] == Decimal("140000.00")


class TestCA0318RetroactiveInitialLoad:
    """CA-03-18: contrato con carga inicial retroactiva (issue #100) --
    los meses anteriores a `current_amount_since` muestran `initial_amount`,
    los posteriores muestran `current_amount` (el ajuste sintetico
    `applied` con `pct_applied=None`)."""

    def test_ca_03_18_synthetic_initial_load_adjustment_shifts_amount(self):
        rows = compute_monthly_amounts(
            status="active",
            start_date=date(2025, 6, 1),
            end_date=date(2027, 6, 1),
            initial_amount=Decimal("100000.00"),
            applied_adjustments=[
                AppliedAdjustment(due_period=date(2026, 2, 1), new_amount=Decimal("150000.00")),
            ],
            today=date(2026, 4, 1),
            terminated_at=None,
        )

        by_period = {row.period: row.amount for row in rows}
        assert by_period[date(2025, 6, 1)] == Decimal("100000.00")
        assert by_period[date(2026, 1, 1)] == Decimal("100000.00")
        assert by_period[date(2026, 2, 1)] == Decimal("150000.00")
        assert by_period[date(2026, 4, 1)] == Decimal("150000.00")


class TestCA0319TerminatedContractCutsAtEffectiveTerminationMonth:
    """CA-03-19: contrato `terminated` corta la serie en el mes de la
    terminacion efectiva (evento `contract.terminated` de `audit_logs`),
    no en `end_date` (que sigue siendo la vigencia originalmente pactada,
    todavia en el futuro)."""

    def test_ca_03_19_terminated_contract_cuts_at_terminated_at_not_end_date(self):
        rows = compute_monthly_amounts(
            status="terminated",
            start_date=date(2026, 1, 1),
            end_date=date(2027, 12, 1),  # vigencia pactada, muy posterior
            initial_amount=Decimal("100000.00"),
            applied_adjustments=[],
            today=date(2027, 6, 1),  # "hoy" tambien muy posterior
            terminated_at=date(2026, 4, 20),
        )

        periods = [row.period for row in rows]
        assert periods[0] == date(2026, 4, 1)
        assert date(2026, 5, 1) not in periods
        assert periods[-1] == date(2026, 1, 1)

    def test_terminated_without_terminated_at_falls_back_to_end_date(self):
        """Defensivo (RN-09): si por algun motivo no existe el evento
        `contract.terminated` en `audit_logs`, el fallback documentado es
        `end_date`."""
        rows = compute_monthly_amounts(
            status="terminated",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 3, 1),
            initial_amount=Decimal("100000.00"),
            applied_adjustments=[],
            today=date(2027, 1, 1),
            terminated_at=None,
        )

        periods = [row.period for row in rows]
        assert periods == [date(2026, 3, 1), date(2026, 2, 1), date(2026, 1, 1)]


class TestCA0320ContractStartingThisMonth:
    """CA-03-20: contrato cuyo `start_date` cae en el mes actual devuelve
    exactamente 1 elemento."""

    def test_ca_03_20_contract_starting_this_month_returns_single_element(self):
        rows = compute_monthly_amounts(
            status="active",
            start_date=date(2026, 4, 15),
            end_date=date(2027, 4, 15),
            initial_amount=Decimal("50000.00"),
            applied_adjustments=[],
            today=date(2026, 4, 28),
            terminated_at=None,
        )

        assert len(rows) == 1
        assert rows[0].period == date(2026, 4, 1)
        assert rows[0].amount == Decimal("50000.00")

    def test_contract_not_yet_started_returns_empty_list(self):
        """`start_date` futuro (contrato `draft` que todavia no arranco)
        -- `monthly_amounts` es `[]` (RF-06)."""
        rows = compute_monthly_amounts(
            status="draft",
            start_date=date(2026, 8, 1),
            end_date=date(2027, 8, 1),
            initial_amount=Decimal("50000.00"),
            applied_adjustments=[],
            today=date(2026, 4, 1),
            terminated_at=None,
        )

        assert rows == []


class TestCA0321DescendingOrder:
    """CA-03-21: `monthly_amounts[]` viene siempre en orden estrictamente
    descendente por `period`."""

    def test_ca_03_21_periods_are_strictly_descending(self):
        rows = compute_monthly_amounts(
            status="active",
            start_date=date(2025, 10, 1),
            end_date=date(2027, 1, 1),
            initial_amount=Decimal("80000.00"),
            applied_adjustments=[
                AppliedAdjustment(due_period=date(2026, 1, 1), new_amount=Decimal("90000.00")),
            ],
            today=date(2026, 3, 1),
            terminated_at=None,
        )

        periods = [row.period for row in rows]
        assert periods == sorted(periods, reverse=True)
        assert len(periods) == len(set(periods))


class TestCA0322UsdFlatSeriesWithoutInitialLoad:
    """CA-03-22: contrato USD sin carga inicial declarada -- serie plana
    en `initial_amount` (RN-03/RN-C02: sin ajuste periodico automatico).
    El calculo no distingue moneda -- solo depende de que no haya ajustes
    `applied`, que es siempre el caso para USD sin RN-08/RN-C06."""

    def test_ca_03_22_usd_contract_without_adjustments_is_flat(self):
        rows = compute_monthly_amounts(
            status="active",
            start_date=date(2026, 1, 1),
            end_date=date(2027, 1, 1),
            initial_amount=Decimal("500.00"),
            applied_adjustments=[],
            today=date(2026, 3, 1),
            terminated_at=None,
        )

        assert all(row.amount == Decimal("500.00") for row in rows)

    def test_usd_contract_with_synthetic_initial_load_still_shifts_amount(self):
        """RN-08/RN-C06: la carga inicial SI aplica a USD -- el ajuste
        sintetico `applied` mueve el monto igual que en ARS."""
        rows = compute_monthly_amounts(
            status="active",
            start_date=date(2026, 1, 1),
            end_date=date(2027, 1, 1),
            initial_amount=Decimal("500.00"),
            applied_adjustments=[
                AppliedAdjustment(due_period=date(2026, 2, 1), new_amount=Decimal("650.00")),
            ],
            today=date(2026, 3, 1),
            terminated_at=None,
        )

        by_period = {row.period: row.amount for row in rows}
        assert by_period[date(2026, 1, 1)] == Decimal("500.00")
        assert by_period[date(2026, 2, 1)] == Decimal("650.00")
        assert by_period[date(2026, 3, 1)] == Decimal("650.00")


class TestExpiredContractUsesEndDate:
    """Un contrato `expired` (vencimiento natural) usa `end_date`
    directamente como mes de corte -- sin ambiguedad, distinto del caso
    `terminated` (terminacion anticipada)."""

    def test_expired_contract_cuts_at_end_date_even_if_today_is_later(self):
        rows = compute_monthly_amounts(
            status="expired",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 5, 1),
            initial_amount=Decimal("70000.00"),
            applied_adjustments=[],
            today=date(2026, 9, 1),
            terminated_at=None,
        )

        periods = [row.period for row in rows]
        assert periods[0] == date(2026, 5, 1)
