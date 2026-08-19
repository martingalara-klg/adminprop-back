"""tests/integration/payments/test_register_payment.py -- issue #22.

SDD: spec_module_04_cobranzas.md §RF-03/RF-04 + sdd_03 §9
`POST /rent-periods/:id/payments`.
"""

from __future__ import annotations

import pytest


async def _seed_contract_and_rent_period(
    seed,
    *,
    grace_day: int = 10,
    currency: str = "ARS",
    daily_late_fee_pct: str = "1.0",
    amount_due: str = "1000.00",
    status: str = "pending",
    paid_total: str = "0.00",
    period: str = "2026-06-01",
):
    org = await seed.create_organization_with_system_roles(grace_day=grace_day)
    owner = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["owner"],
        role_name="owner",
    )
    landlord_id = await seed.create_landlord_row(organization_id=org["organization_id"])
    renter_id = await seed.create_renter_row(organization_id=org["organization_id"])
    property_id = await seed.create_property_row(
        organization_id=org["organization_id"], landlord_id=landlord_id
    )
    contract_id = await seed.create_contract_row(
        organization_id=org["organization_id"],
        property_id=property_id,
        renter_id=renter_id,
        currency=currency,
        daily_late_fee_pct=daily_late_fee_pct,
    )
    rent_period_id = await seed.create_rent_period_row(
        organization_id=org["organization_id"],
        contract_id=contract_id,
        period=period,
        amount_due=amount_due,
        currency=currency,
        status=status,
        paid_total=paid_total,
    )
    return org, owner, rent_period_id


class TestExchangeRateRequired:
    """CA-04-03: contrato USD pagado en pesos exige `exchange_rate`."""

    @pytest.mark.asyncio
    async def test_ca_04_03_payment_currency_differs_without_exchange_rate_returns_400(
        self, client, seed
    ):
        """CA-04-03: "un cobro de contrato USD pagado en pesos sin
        `exchange_rate` devuelve `400 EXCHANGE_RATE_REQUIRED`"."""
        _, owner, rent_period_id = await _seed_contract_and_rent_period(
            seed, currency="USD", amount_due="1000.00"
        )

        response = await client.post(
            f"/v1/rent-periods/{rent_period_id}/payments",
            json={
                "payment_date": "2026-06-05",
                "method": "cash",
                "payment_currency": "ARS",
                "amount": "500.00",
                "destination": "agency_account",
                "charged_interest": "0.00",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "EXCHANGE_RATE_REQUIRED"

    @pytest.mark.asyncio
    async def test_ca_04_03_payment_with_exchange_rate_persists_the_rate_used(self, client, seed):
        """CA-04-03: "con TC, el cobro registra el TC usado"."""
        _, owner, rent_period_id = await _seed_contract_and_rent_period(
            seed, currency="USD", amount_due="1000.00"
        )

        response = await client.post(
            f"/v1/rent-periods/{rent_period_id}/payments",
            json={
                "payment_date": "2026-06-05",
                "method": "cash",
                "payment_currency": "ARS",
                "amount": "500.00",
                "exchange_rate": "1200.5000",
                "destination": "agency_account",
                "charged_interest": "0.00",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["exchange_rate"] == "1200.5000"
        assert data["payment_currency"] == "ARS"
        assert data["amount"] == "500.00"

    @pytest.mark.asyncio
    async def test_same_currency_payment_does_not_require_exchange_rate(self, client, seed):
        _, owner, rent_period_id = await _seed_contract_and_rent_period(
            seed, currency="ARS", amount_due="1000.00"
        )

        response = await client.post(
            f"/v1/rent-periods/{rent_period_id}/payments",
            json={
                "payment_date": "2026-06-05",
                "method": "transfer",
                "payment_currency": "ARS",
                "amount": "500.00",
                "destination": "agency_account",
                "charged_interest": "0.00",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 201
        assert response.json()["data"]["exchange_rate"] is None


class TestPartialPayments:
    """CA-04-04: pagos parciales -- estado `partial` + interes sobre saldo."""

    @pytest.mark.asyncio
    async def test_ca_04_04_partial_payment_leaves_rent_period_in_partial_status(
        self, client, seed
    ):
        """CA-04-04: "un pago parcial deja el periodo en `partial`"."""
        _, owner, rent_period_id = await _seed_contract_and_rent_period(seed, amount_due="1000.00")

        response = await client.post(
            f"/v1/rent-periods/{rent_period_id}/payments",
            json={
                "payment_date": "2026-06-05",
                "method": "cash",
                "payment_currency": "ARS",
                "amount": "400.00",
                "destination": "agency_account",
                "charged_interest": "0.00",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 201
        rent_period = await seed.get_rent_period(rent_period_id)
        assert rent_period["status"] == "partial"
        assert rent_period["paid_total"] == "400.00"

    @pytest.mark.asyncio
    async def test_ca_04_04_second_payment_interest_computed_only_on_remaining_balance(
        self, client, seed
    ):
        """CA-04-04: "el interes de un pago posterior se calcula solo
        sobre el saldo restante"."""
        _, owner, rent_period_id = await _seed_contract_and_rent_period(
            seed, amount_due="1000.00", status="partial", paid_total="400.00"
        )

        response = await client.post(
            f"/v1/rent-periods/{rent_period_id}/payments",
            json={
                "payment_date": "2026-06-15",
                "method": "cash",
                "payment_currency": "ARS",
                "amount": "600.00",
                "destination": "agency_account",
                "charged_interest": "30.00",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 201
        data = response.json()["data"]
        # RN-P03: saldo restante (600.00) x 1% x 5 dias = 30.00 -- NO sobre
        # el amount_due completo (1000.00, que hubiera dado 50.00).
        assert data["suggested_interest"] == "30.00"
        assert data["charged_interest"] == "30.00"
        assert data["forgiven_interest"] == "0.00"

        rent_period = await seed.get_rent_period(rent_period_id)
        assert rent_period["status"] == "paid"
        assert rent_period["paid_total"] == "1000.00"

    @pytest.mark.asyncio
    async def test_payment_exceeding_balance_returns_422(self, client, seed):
        _, owner, rent_period_id = await _seed_contract_and_rent_period(seed, amount_due="1000.00")

        response = await client.post(
            f"/v1/rent-periods/{rent_period_id}/payments",
            json={
                "payment_date": "2026-06-05",
                "method": "cash",
                "payment_currency": "ARS",
                "amount": "1000.01",
                "destination": "agency_account",
                "charged_interest": "0.00",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "PAYMENT_EXCEEDS_CONTRACT_BALANCE"

    @pytest.mark.asyncio
    async def test_payment_on_already_paid_rent_period_returns_422(self, client, seed):
        _, owner, rent_period_id = await _seed_contract_and_rent_period(
            seed, amount_due="1000.00", status="paid", paid_total="1000.00"
        )

        response = await client.post(
            f"/v1/rent-periods/{rent_period_id}/payments",
            json={
                "payment_date": "2026-06-05",
                "method": "cash",
                "payment_currency": "ARS",
                "amount": "100.00",
                "destination": "agency_account",
                "charged_interest": "0.00",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "RENT_PERIOD_ALREADY_PAID"


class TestSuggestedInterestAndForgiveness:
    """CA-04-05/CA-04-06: mora sugerida con perdon total/parcial, auditada."""

    @pytest.mark.asyncio
    async def test_ca_04_05_operator_can_forgive_interest_totally(self, client, seed):
        """CA-04-05: "el operador puede imputar 0 (perdon total)... y
        quedan registrados sugerido/cobrado/perdonado"."""
        _, owner, rent_period_id = await _seed_contract_and_rent_period(
            seed, amount_due="1000.00", grace_day=10
        )

        response = await client.post(
            f"/v1/rent-periods/{rent_period_id}/payments",
            json={
                "payment_date": "2026-06-15",
                "method": "cash",
                "payment_currency": "ARS",
                "amount": "1000.00",
                "destination": "agency_account",
                "charged_interest": "0.00",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["suggested_interest"] == "50.00"
        assert data["charged_interest"] == "0.00"
        assert data["forgiven_interest"] == "50.00"

    @pytest.mark.asyncio
    async def test_ca_04_05_operator_can_forgive_interest_partially(self, client, seed):
        """CA-04-05: "o un valor menor (perdon parcial)"."""
        _, owner, rent_period_id = await _seed_contract_and_rent_period(
            seed, amount_due="1000.00", grace_day=10
        )

        response = await client.post(
            f"/v1/rent-periods/{rent_period_id}/payments",
            json={
                "payment_date": "2026-06-15",
                "method": "cash",
                "payment_currency": "ARS",
                "amount": "1000.00",
                "destination": "agency_account",
                "charged_interest": "20.00",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["suggested_interest"] == "50.00"
        assert data["charged_interest"] == "20.00"
        assert data["forgiven_interest"] == "30.00"

    @pytest.mark.asyncio
    async def test_ca_04_06_total_forgiveness_is_audited_with_author_and_payment(
        self, client, seed
    ):
        """CA-04-06: "todo perdon de interes queda en el log de auditoria
        con autor y cobro asociado"."""
        org, owner, rent_period_id = await _seed_contract_and_rent_period(
            seed, amount_due="1000.00", grace_day=10
        )

        response = await client.post(
            f"/v1/rent-periods/{rent_period_id}/payments",
            json={
                "payment_date": "2026-06-15",
                "method": "cash",
                "payment_currency": "ARS",
                "amount": "1000.00",
                "destination": "agency_account",
                "charged_interest": "0.00",
            },
            headers=owner["headers"],
        )
        assert response.status_code == 201
        payment_id = response.json()["data"]["id"]

        rows = await seed.audit_rows(org["organization_id"], "interest.forgiven")
        assert len(rows) == 1
        assert str(rows[0]["entity_id"]) == payment_id
        assert str(rows[0]["user_id"]) == str(owner["id"])
        assert rows[0]["after_state"]["forgiven_interest"] == "50.00"

    @pytest.mark.asyncio
    async def test_no_forgiveness_when_charged_interest_equals_suggested(self, client, seed):
        """Sin perdon (cobrado == sugerido) -- no debe auditarse
        `interest.forgiven` (CA-04-06 solo aplica si `forgiven > 0`)."""
        org, owner, rent_period_id = await _seed_contract_and_rent_period(
            seed, amount_due="1000.00", grace_day=10
        )

        response = await client.post(
            f"/v1/rent-periods/{rent_period_id}/payments",
            json={
                "payment_date": "2026-06-15",
                "method": "cash",
                "payment_currency": "ARS",
                "amount": "1000.00",
                "destination": "agency_account",
                "charged_interest": "50.00",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 201
        assert response.json()["data"]["forgiven_interest"] == "0.00"
        rows = await seed.audit_rows(org["organization_id"], "interest.forgiven")
        assert rows == []

    @pytest.mark.asyncio
    async def test_charged_interest_above_suggested_is_not_a_negative_forgiveness(
        self, client, seed
    ):
        """Validaciones: "el sistema no impone tope" -- cobrar mas que lo
        sugerido no genera un `forgiven_interest` negativo."""
        _, owner, rent_period_id = await _seed_contract_and_rent_period(
            seed, amount_due="1000.00", grace_day=10
        )

        response = await client.post(
            f"/v1/rent-periods/{rent_period_id}/payments",
            json={
                "payment_date": "2026-06-15",
                "method": "cash",
                "payment_currency": "ARS",
                "amount": "1000.00",
                "destination": "agency_account",
                "charged_interest": "80.00",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["charged_interest"] == "80.00"
        assert data["forgiven_interest"] == "0.00"


class TestPaymentDestination:
    """RN-P07: destino del cobro."""

    @pytest.mark.asyncio
    async def test_landlord_account_destination_is_persisted(self, client, seed):
        _, owner, rent_period_id = await _seed_contract_and_rent_period(seed, amount_due="1000.00")

        response = await client.post(
            f"/v1/rent-periods/{rent_period_id}/payments",
            json={
                "payment_date": "2026-06-05",
                "method": "transfer",
                "payment_currency": "ARS",
                "amount": "1000.00",
                "destination": "landlord_account",
                "charged_interest": "0.00",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 201
        assert response.json()["data"]["destination"] == "landlord_account"


class TestValidations:
    """Validaciones del schema (fuera del catalogo de error.code)."""

    @pytest.mark.asyncio
    async def test_future_payment_date_returns_400(self, client, seed):
        _, owner, rent_period_id = await _seed_contract_and_rent_period(seed, amount_due="1000.00")

        response = await client.post(
            f"/v1/rent-periods/{rent_period_id}/payments",
            json={
                "payment_date": "2999-01-01",
                "method": "cash",
                "payment_currency": "ARS",
                "amount": "100.00",
                "destination": "agency_account",
                "charged_interest": "0.00",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_maintenance_role_cannot_register_payment(self, client, seed):
        """RN-A01: `maintenance` no tiene `payment:create`."""
        org = await seed.create_organization_with_system_roles()
        maintenance = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["maintenance"],
            role_name="maintenance",
        )
        landlord_id = await seed.create_landlord_row(organization_id=org["organization_id"])
        renter_id = await seed.create_renter_row(organization_id=org["organization_id"])
        property_id = await seed.create_property_row(
            organization_id=org["organization_id"], landlord_id=landlord_id
        )
        contract_id = await seed.create_contract_row(
            organization_id=org["organization_id"], property_id=property_id, renter_id=renter_id
        )
        rent_period_id = await seed.create_rent_period_row(
            organization_id=org["organization_id"], contract_id=contract_id
        )

        response = await client.post(
            f"/v1/rent-periods/{rent_period_id}/payments",
            json={
                "payment_date": "2026-06-05",
                "method": "cash",
                "payment_currency": "ARS",
                "amount": "100.00",
                "destination": "agency_account",
                "charged_interest": "0.00",
            },
            headers=maintenance["headers"],
        )

        assert response.status_code == 403
