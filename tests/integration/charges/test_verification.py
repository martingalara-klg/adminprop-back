"""tests/integration/charges/test_verification.py -- issue #28, CA-05-08.

SDD: docs/sdd/features/spec_module_05_liquidaciones.md §RF-05 "Vista de
verificacion" -- "GET /charge-entries?period= muestra que propiedades ya
tienen sus cargos del mes y cuales faltan -- el checklist mensual de la
secretaria".
"""

from __future__ import annotations

import pytest


class TestCA0508ChargeEntriesVerification:
    """spec_module_05_liquidaciones.md §RF-05, CA-05-08."""

    @pytest.mark.asyncio
    async def test_ca_05_08_shows_loaded_and_missing_properties_for_period(self, client, seed):
        """CA-05-08: "muestra las propiedades con cargos cargados y las
        que faltan"."""
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )
        landlord_id = await seed.create_landlord_row(organization_id=org["organization_id"])
        property_loaded_id = await seed.create_property_row(
            organization_id=org["organization_id"],
            landlord_id=landlord_id,
            address="Propiedad con cargo cargado",
        )
        property_missing_id = await seed.create_property_row(
            organization_id=org["organization_id"],
            landlord_id=landlord_id,
            address="Propiedad sin cargo cargado",
        )
        charge_loaded_id = await seed.create_recurring_charge_row(
            organization_id=org["organization_id"], property_id=property_loaded_id
        )
        charge_missing_id = await seed.create_recurring_charge_row(
            organization_id=org["organization_id"], property_id=property_missing_id
        )
        entry_id = await seed.create_charge_entry_row(
            organization_id=org["organization_id"],
            recurring_charge_id=charge_loaded_id,
            created_by=owner["id"],
            period="2026-06-01",
            amount="7000.00",
        )

        response = await client.get(
            "/v1/charge-entries", params={"period": "2026-06"}, headers=owner["headers"]
        )

        assert response.status_code == 200
        items = {item["recurring_charge_id"]: item for item in response.json()["data"]}

        loaded_item = items[str(charge_loaded_id)]
        assert loaded_item["has_entry"] is True
        assert loaded_item["property_id"] == str(property_loaded_id)
        assert loaded_item["charge_entry_id"] == str(entry_id)
        assert loaded_item["amount"] == "7000.00"

        missing_item = items[str(charge_missing_id)]
        assert missing_item["has_entry"] is False
        assert missing_item["property_id"] == str(property_missing_id)
        assert missing_item["charge_entry_id"] is None
        assert missing_item["amount"] is None

    @pytest.mark.asyncio
    async def test_ca_05_08_inactive_recurring_charge_is_excluded_from_monthly_load(
        self, client, seed
    ):
        """RF-05 §1: "un concepto inactivo deja de aparecer en la carga
        mensual" -- no aparece ni como cargado ni como faltante."""
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )
        landlord_id = await seed.create_landlord_row(organization_id=org["organization_id"])
        property_id = await seed.create_property_row(
            organization_id=org["organization_id"], landlord_id=landlord_id
        )
        inactive_charge_id = await seed.create_recurring_charge_row(
            organization_id=org["organization_id"], property_id=property_id, is_active=False
        )

        response = await client.get(
            "/v1/charge-entries", params={"period": "2026-06"}, headers=owner["headers"]
        )

        assert response.status_code == 200
        ids = {item["recurring_charge_id"] for item in response.json()["data"]}
        assert str(inactive_charge_id) not in ids

    @pytest.mark.asyncio
    async def test_ca_05_08_verification_requires_period_query_param(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )

        response = await client.get("/v1/charge-entries", headers=owner["headers"])

        # RequestValidationError se mapea al formato custom con 400
        # VALIDATION_ERROR (ver shared/errors/handlers.py), no 422.
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_ca_05_08_verification_rejects_invalid_period_format(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )

        response = await client.get(
            "/v1/charge-entries", params={"period": "06-2026"}, headers=owner["headers"]
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
