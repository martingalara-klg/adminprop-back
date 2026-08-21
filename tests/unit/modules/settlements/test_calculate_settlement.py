"""Tests unitarios de `adminprop.modules.settlements.service.
calculate_settlement` -- formula pura de RF-02, sin DB (issue #29).

SDD: docs/sdd/features/spec_module_05_liquidaciones.md §RF-02.
Implements: CA-05-01, CA-05-02, CA-05-04/CA-04-08.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from adminprop.modules.settlements.repository import (
    GatheredSettlementData,
    SettlementChargeEntryRow,
    SettlementPaymentRow,
    SettlementRepairRow,
)
from adminprop.modules.settlements.service import calculate_settlement, round2

_PROPERTY_A = uuid.uuid4()
_PROPERTY_B = uuid.uuid4()


def _empty_data(**overrides) -> GatheredSettlementData:
    defaults = {"payments": [], "charge_entries": [], "repairs": []}
    defaults.update(overrides)
    return GatheredSettlementData(**defaults)


class TestRound2HalfEven:
    """CA-05-01: "redondeo half-even a 2 decimales"."""

    def test_rounds_half_to_even_up(self):
        assert round2(Decimal("1.005")) == Decimal("1.00")

    def test_rounds_half_to_even_up_other_direction(self):
        assert round2(Decimal("1.015")) == Decimal("1.02")

    def test_no_rounding_needed_is_unchanged(self):
        assert round2(Decimal("100.10")) == Decimal("100.10")


class TestCa0501FormulaTwoArsProperties:
    """CA-05-01: "liquidación de un propietario con 2 propiedades ARS
    consolida cobros − comisión (con su commission_pct) − cargos −
    reparaciones agency, y el neto coincide con la fórmula a centavo"."""

    def test_ca_05_01_net_amount_matches_formula_to_the_cent(self):
        payment_a_id = uuid.uuid4()
        payment_b_id = uuid.uuid4()
        charge_id = uuid.uuid4()
        repair_id = uuid.uuid4()

        data = _empty_data(
            payments=[
                SettlementPaymentRow(
                    payment_id=payment_a_id,
                    property_id=_PROPERTY_A,
                    currency="ARS",
                    amount=Decimal("100000.00"),
                    charged_interest=Decimal("0.00"),
                    destination="agency_account",
                ),
                SettlementPaymentRow(
                    payment_id=payment_b_id,
                    property_id=_PROPERTY_B,
                    currency="ARS",
                    amount=Decimal("80000.00"),
                    charged_interest=Decimal("1500.50"),
                    destination="agency_account",
                ),
            ],
            charge_entries=[
                SettlementChargeEntryRow(
                    charge_entry_id=charge_id, property_id=_PROPERTY_A, amount=Decimal("5000.00")
                )
            ],
            repairs=[
                SettlementRepairRow(
                    work_order_id=repair_id, property_id=_PROPERTY_B, final_cost=Decimal("2000.00")
                )
            ],
        )

        result = calculate_settlement(
            data=data,
            commission_pct=Decimal("10.00"),
            exchange_rate=None,
            unpaid_periods=[],
            missing_charges=[],
        )

        total_collected = Decimal("100000.00") + Decimal("80000.00") + Decimal("1500.50")
        commission_total = round2(total_collected * Decimal("10.00") / Decimal(100))
        expected_net = total_collected - commission_total - Decimal("5000.00") - Decimal("2000.00")

        assert result.total_collected == round2(total_collected)
        assert result.commission_total == commission_total
        assert result.charges_total == Decimal("5000.00")
        assert result.repairs_total == Decimal("2000.00")
        assert result.net_amount == round2(expected_net)
        assert result.warnings == []
        assert result.settled_work_order_ids == [repair_id]

        line_types = {item.line_type for item in result.line_items}
        assert line_types == {"rent_collected", "commission", "tax_charge", "repair"}


class TestCa0502UsdConversion:
    """CA-05-02: "con TC, el detalle muestra el monto USD original y el
    convertido, y los totales quedan en ARS"."""

    def test_ca_05_02_usd_payment_is_converted_and_original_preserved(self):
        payment_id = uuid.uuid4()
        data = _empty_data(
            payments=[
                SettlementPaymentRow(
                    payment_id=payment_id,
                    property_id=_PROPERTY_A,
                    currency="USD",
                    amount=Decimal("500.00"),
                    charged_interest=Decimal("0.00"),
                    destination="agency_account",
                )
            ]
        )

        result = calculate_settlement(
            data=data,
            commission_pct=Decimal("10.00"),
            exchange_rate=Decimal("1000.0000"),
            unpaid_periods=[],
            missing_charges=[],
        )

        rent_line = next(item for item in result.line_items if item.line_type == "rent_collected")
        assert rent_line.original_amount == Decimal("500.00")
        assert rent_line.original_currency == "USD"
        assert rent_line.amount_ars == round2(Decimal("500.00") * Decimal("1000.0000"))
        assert result.total_collected == rent_line.amount_ars
        # RN-L06: totales SIEMPRE en ARS.
        assert result.commission_total == round2(
            result.total_collected * Decimal("10.00") / Decimal(100)
        )


class TestCa0504AlreadySettled:
    """CA-05-04/CA-04-08: cobro "ya rendido" -- linea informativa que
    integra la base de comision pero no el neto (RN-L01/RN-P07)."""

    def test_ca_05_04_landlord_account_payment_is_already_settled_line(self):
        payment_id = uuid.uuid4()
        data = _empty_data(
            payments=[
                SettlementPaymentRow(
                    payment_id=payment_id,
                    property_id=_PROPERTY_A,
                    currency="ARS",
                    amount=Decimal("50000.00"),
                    charged_interest=Decimal("0.00"),
                    destination="landlord_account",
                )
            ]
        )

        result = calculate_settlement(
            data=data,
            commission_pct=Decimal("10.00"),
            exchange_rate=None,
            unpaid_periods=[],
            missing_charges=[],
        )

        # RN-L01: no suma al "neto a rendir" (total_collected solo cuenta
        # destino administracion).
        assert result.total_collected == Decimal("0.00")
        assert result.already_settled_total == Decimal("50000.00")
        # RN-L02: pero SI integra la base de comision (incluidos los
        # cobrados directo por el dueño).
        assert result.commission_total == round2(Decimal("50000.00") * Decimal("10.00") / 100)

        line = next(item for item in result.line_items if item.line_type == "already_settled")
        assert line.source_entity_id == payment_id
        assert line.amount_ars == Decimal("50000.00")

    def test_commission_base_includes_both_administration_and_landlord_account(self):
        """RN-L02: "incluidos los cobrados directo por el dueño"."""
        admin_payment_id = uuid.uuid4()
        direct_payment_id = uuid.uuid4()
        data = _empty_data(
            payments=[
                SettlementPaymentRow(
                    payment_id=admin_payment_id,
                    property_id=_PROPERTY_A,
                    currency="ARS",
                    amount=Decimal("30000.00"),
                    charged_interest=Decimal("0.00"),
                    destination="agency_account",
                ),
                SettlementPaymentRow(
                    payment_id=direct_payment_id,
                    property_id=_PROPERTY_B,
                    currency="ARS",
                    amount=Decimal("20000.00"),
                    charged_interest=Decimal("0.00"),
                    destination="landlord_account",
                ),
            ]
        )

        result = calculate_settlement(
            data=data,
            commission_pct=Decimal("10.00"),
            exchange_rate=None,
            unpaid_periods=[],
            missing_charges=[],
        )

        expected_commission = round2(Decimal("50000.00") * Decimal("10.00") / 100)
        assert result.commission_total == expected_commission


class TestWarnings:
    """CA-05-03: "con periodos impagos o cargos faltantes termina
    with_errors y las advertencias se listan en el detalle"."""

    def test_no_warnings_when_nothing_pending(self):
        result = calculate_settlement(
            data=_empty_data(),
            commission_pct=Decimal("10.00"),
            exchange_rate=None,
            unpaid_periods=[],
            missing_charges=[],
        )
        assert result.warnings == []

    def test_unpaid_period_and_missing_charge_produce_warnings(self):
        from adminprop.modules.settlements.repository import (
            MissingChargeEntryRow,
            UnpaidRentPeriodRow,
        )

        result = calculate_settlement(
            data=_empty_data(),
            commission_pct=Decimal("10.00"),
            exchange_rate=None,
            unpaid_periods=[
                UnpaidRentPeriodRow(
                    rent_period_id=uuid.uuid4(), property_id=_PROPERTY_A, status="pending"
                )
            ],
            missing_charges=[
                MissingChargeEntryRow(
                    recurring_charge_id=uuid.uuid4(), property_id=_PROPERTY_B, label="Rentas"
                )
            ],
        )
        assert len(result.warnings) == 2
