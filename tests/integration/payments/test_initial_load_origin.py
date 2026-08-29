"""tests/integration/payments/test_initial_load_origin.py -- issue #119.

SDD: docs/sdd/features/spec_module_04_cobranzas.md RF-03/RF-05/RF-07 (RN-08)
     + core/sdd_02_domain_model.md §3 RN-P09.
Implements: CA-04-13, CA-04-14, CA-04-15.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _seed_initial_load_payment(seed, *, amount: str = "1000.00"):
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
        amount_due=amount,
        paid_total=amount,
        status="paid",
    )
    payment_id = await seed.create_payment_row(
        organization_id=org["organization_id"],
        rent_period_id=rent_period_id,
        created_by=owner["id"],
        amount=amount,
        destination="landlord_account",
        origin="initial_load",
    )
    return org, owner, rent_period_id, payment_id


class TestCA0413PanelExposesOrigin:
    """CA-04-13: el panel de cobranzas de un mes pasado muestra el periodo
    `paid`; `GET /rent-periods/:id` expone `origin` en `payments[]`."""

    async def test_ca_04_13_rent_period_detail_exposes_initial_load_origin(self, client, seed):
        _org, owner, rent_period_id, payment_id = await _seed_initial_load_payment(seed)

        response = await client.get(
            f"/v1/rent-periods/{rent_period_id}", headers=owner["headers"]
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "paid"
        assert len(data["payments"]) == 1
        payment = data["payments"][0]
        assert payment["id"] == str(payment_id)
        assert payment["origin"] == "initial_load"

    async def test_ca_04_13_manual_payment_exposes_manual_origin(self, client, seed):
        """Contraparte: un cobro registrado normalmente sigue exponiendo
        `origin: "manual"` (default) -- no hay regresion en el shape."""
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
        await seed.create_payment_row(
            organization_id=org["organization_id"],
            rent_period_id=rent_period_id,
            created_by=owner["id"],
            amount="1000.00",
        )

        response = await client.get(
            f"/v1/rent-periods/{rent_period_id}", headers=owner["headers"]
        )
        assert response.status_code == 200
        assert response.json()["data"]["payments"][0]["origin"] == "manual"


class TestCA0414ReceiptRejectsInitialLoad:
    """CA-04-14: `GET /payments/:id/receipt` sobre un cobro
    `origin = initial_load` devuelve `422 BUSINESS_RULE_VIOLATION`."""

    async def test_ca_04_14_receipt_on_initial_load_payment_returns_422(self, client, seed):
        _org, owner, _rent_period_id, payment_id = await _seed_initial_load_payment(seed)

        response = await client.get(f"/v1/payments/{payment_id}/receipt", headers=owner["headers"])

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "BUSINESS_RULE_VIOLATION"


class TestCA0415VoidRejectsInitialLoad:
    """CA-04-15: `POST /payments/:id/void` sobre un cobro
    `origin = initial_load` devuelve `422 BUSINESS_RULE_VIOLATION`."""

    async def test_ca_04_15_void_on_initial_load_payment_returns_422(self, client, seed):
        _org, owner, _rent_period_id, payment_id = await _seed_initial_load_payment(seed)

        response = await client.post(
            f"/v1/payments/{payment_id}/void",
            json={"reason": "Intento de anular un cobro de carga inicial"},
            headers=owner["headers"],
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "BUSINESS_RULE_VIOLATION"
