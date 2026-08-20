"""tests/integration/charges/test_recurring_charges.py -- issue #28, RF-05 §1.

SDD: docs/sdd/features/spec_module_05_liquidaciones.md §RF-05
"ABM de conceptos por propiedad".
"""

from __future__ import annotations

import pytest


class TestRF05RecurringChargesAbm:
    """spec_module_05_liquidaciones.md §RF-05 -- ABM de conceptos
    recurrentes por propiedad."""

    @pytest.mark.asyncio
    async def test_rf05_01_create_recurring_charge_for_property(self, client, seed):
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

        response = await client.post(
            f"/v1/properties/{property_id}/recurring-charges",
            json={"charge_type": "rentas", "label": "Rentas Cordoba"},
            headers=owner["headers"],
        )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["property_id"] == str(property_id)
        assert data["charge_type"] == "rentas"
        assert data["label"] == "Rentas Cordoba"
        assert data["is_active"] is True

    @pytest.mark.asyncio
    async def test_rf05_02_create_recurring_charge_for_unknown_property_returns_404(
        self, client, seed
    ):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )
        fake_property_id = "00000000-0000-0000-0000-000000000000"

        response = await client.post(
            f"/v1/properties/{fake_property_id}/recurring-charges",
            json={"charge_type": "municipalidad", "label": "Municipalidad"},
            headers=owner["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_rf05_03_list_recurring_charges_includes_inactive(self, client, seed):
        """RF-05: "un concepto inactivo deja de aparecer en la carga
        mensual" -- pero el ABM lista activos e inactivos juntos."""
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
        active_id = await seed.create_recurring_charge_row(
            organization_id=org["organization_id"], property_id=property_id, is_active=True
        )
        inactive_id = await seed.create_recurring_charge_row(
            organization_id=org["organization_id"], property_id=property_id, is_active=False
        )

        response = await client.get(
            f"/v1/properties/{property_id}/recurring-charges", headers=owner["headers"]
        )

        assert response.status_code == 200
        ids = {item["id"] for item in response.json()["data"]}
        assert str(active_id) in ids
        assert str(inactive_id) in ids

    @pytest.mark.asyncio
    async def test_rf05_04_patch_recurring_charge_updates_label_and_is_active(self, client, seed):
        """sdd_03 §10: `PATCH /recurring-charges/:id (label, is_active)`."""
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
        recurring_charge_id = await seed.create_recurring_charge_row(
            organization_id=org["organization_id"], property_id=property_id
        )

        response = await client.patch(
            f"/v1/recurring-charges/{recurring_charge_id}",
            json={"label": "Rentas actualizado", "is_active": False},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["label"] == "Rentas actualizado"
        assert data["is_active"] is False

    @pytest.mark.asyncio
    async def test_rf05_05_maintenance_role_cannot_manage_charges(self, client, seed):
        """RN-A01: `maintenance` no tiene ningun permiso `charge:*`."""
        org = await seed.create_organization_with_system_roles()
        maintenance = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["maintenance"],
            role_name="maintenance",
        )
        landlord_id = await seed.create_landlord_row(organization_id=org["organization_id"])
        property_id = await seed.create_property_row(
            organization_id=org["organization_id"], landlord_id=landlord_id
        )

        response = await client.get(
            f"/v1/properties/{property_id}/recurring-charges", headers=maintenance["headers"]
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"
