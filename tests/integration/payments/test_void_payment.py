"""tests/integration/payments/test_void_payment.py -- issue #23.

SDD: spec_module_04_cobranzas.md §RF-05 + sdd_03 §9 `POST /payments/:id/void`.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _seed_paid_period(seed, *, amount_due: str = "1000.00", paid_total: str = "1000.00"):
    org = await seed.create_organization_with_system_roles()
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
        organization_id=org["organization_id"], property_id=property_id, renter_id=renter_id
    )
    status = "paid" if paid_total == amount_due else "partial"
    rent_period_id = await seed.create_rent_period_row(
        organization_id=org["organization_id"],
        contract_id=contract_id,
        amount_due=amount_due,
        paid_total=paid_total,
        status=status,
    )
    payment_id = await seed.create_payment_row(
        organization_id=org["organization_id"],
        rent_period_id=rent_period_id,
        created_by=owner["id"],
        amount=paid_total,
    )
    return org, owner, rent_period_id, payment_id


class TestVoidPayment:
    """CA-04-07: anular un cobro recompone el saldo del periodo, conserva
    el cobro visible como anulado, y queda auditado con motivo."""

    async def test_ca_04_07_void_recomputes_period_balance_paid_to_pending(self, client, seed):
        """CA-04-07: "recompone el saldo del periodo (paid->partial o
        partial->pending segun corresponda)" -- caso paid->pending cuando
        el cobro anulado cubria todo el periodo."""
        _, owner, rent_period_id, payment_id = await _seed_paid_period(
            seed, amount_due="1000.00", paid_total="1000.00"
        )

        response = await client.post(
            f"/v1/payments/{payment_id}/void",
            json={"reason": "Cheque rechazado por el banco"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["voided_at"] is not None
        assert data["voided_by"] == str(owner["id"])

        rent_period = await seed.get_rent_period(rent_period_id)
        assert rent_period["status"] == "pending"
        assert rent_period["paid_total"] == "0.00"

    async def test_ca_04_07_void_recomputes_period_balance_paid_to_partial(self, client, seed):
        """CA-04-07: caso paid->partial -- otro cobro previo cubre parte
        del periodo y solo se anula uno de los dos."""
        org = await seed.create_organization_with_system_roles()
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
            organization_id=org["organization_id"], property_id=property_id, renter_id=renter_id
        )
        rent_period_id = await seed.create_rent_period_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            amount_due="1000.00",
            paid_total="1000.00",
            status="paid",
        )
        # Dos cobros que en conjunto pagaron el periodo completo.
        await seed.create_payment_row(
            organization_id=org["organization_id"],
            rent_period_id=rent_period_id,
            created_by=owner["id"],
            amount="400.00",
        )
        payment_id = await seed.create_payment_row(
            organization_id=org["organization_id"],
            rent_period_id=rent_period_id,
            created_by=owner["id"],
            amount="600.00",
        )

        response = await client.post(
            f"/v1/payments/{payment_id}/void",
            json={"reason": "Duplicado por error de carga"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        rent_period = await seed.get_rent_period(rent_period_id)
        assert rent_period["status"] == "partial"
        assert rent_period["paid_total"] == "400.00"

    async def test_ca_04_07_voided_payment_still_visible_marked_as_voided(self, client, seed):
        """CA-04-07: "el cobro queda visible con marca de anulado"."""
        _, owner, _, payment_id = await _seed_paid_period(seed)

        void_response = await client.post(
            f"/v1/payments/{payment_id}/void",
            json={"reason": "Error de carga"},
            headers=owner["headers"],
        )
        assert void_response.status_code == 200

        payment_row = await seed.get_payment(payment_id)
        assert payment_row["voided_at"] is not None
        assert str(payment_row["voided_by"]) == str(owner["id"])

    async def test_ca_04_07_void_is_audited_with_author_and_reason(self, client, seed):
        """CA-04-07: "la anulacion se audita con autor y motivo"."""
        org, owner, _, payment_id = await _seed_paid_period(seed)

        response = await client.post(
            f"/v1/payments/{payment_id}/void",
            json={"reason": "El inquilino pago con cheque rechazado"},
            headers=owner["headers"],
        )
        assert response.status_code == 200

        rows = await seed.audit_rows(org["organization_id"], "payment.voided")
        assert len(rows) == 1
        assert str(rows[0]["entity_id"]) == str(payment_id)
        assert str(rows[0]["user_id"]) == str(owner["id"])
        assert rows[0]["after_state"]["reason"] == "El inquilino pago con cheque rechazado"

    async def test_ca_04_07_voiding_twice_returns_409_payment_already_voided(self, client, seed):
        """CA-04-07: "anular dos veces devuelve `409 PAYMENT_ALREADY_VOIDED`"."""
        _, owner, _, payment_id = await _seed_paid_period(seed)

        first = await client.post(
            f"/v1/payments/{payment_id}/void",
            json={"reason": "Primera anulacion"},
            headers=owner["headers"],
        )
        assert first.status_code == 200

        second = await client.post(
            f"/v1/payments/{payment_id}/void",
            json={"reason": "Segunda anulacion"},
            headers=owner["headers"],
        )

        assert second.status_code == 409
        assert second.json()["error"]["code"] == "PAYMENT_ALREADY_VOIDED"

    async def test_void_without_reason_returns_400_validation_error(self, client, seed):
        """RF-05: "motivo obligatorio"."""
        _, owner, _, payment_id = await _seed_paid_period(seed)

        response = await client.post(
            f"/v1/payments/{payment_id}/void",
            json={"reason": ""},
            headers=owner["headers"],
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_void_nonexistent_payment_returns_404(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )

        response = await client.post(
            f"/v1/payments/{uuid.uuid4()}/void",
            json={"reason": "No existe"},
            headers=owner["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_maintenance_role_cannot_void_payment(self, client, seed):
        """RN-A01: `maintenance` no tiene `payment:void`."""
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
        payment_id = await seed.create_payment_row(
            organization_id=org["organization_id"],
            rent_period_id=rent_period_id,
            created_by=maintenance["id"],
        )

        response = await client.post(
            f"/v1/payments/{payment_id}/void",
            json={"reason": "Intento no autorizado"},
            headers=maintenance["headers"],
        )

        assert response.status_code == 403
