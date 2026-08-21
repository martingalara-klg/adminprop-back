"""tests/integration/charges/test_charge_entries.py -- issue #28, RF-05 §2/§3.

SDD: docs/sdd/features/spec_module_05_liquidaciones.md §RF-05 "Carga
mensual" + core/sdd_02_domain_model.md §3 RN-D04 "Las correcciones de
cobros y liquidaciones siempre quedan trazadas en el log de auditoria".
"""

from __future__ import annotations

import pytest


class TestRF05ChargeEntryCreation:
    """spec_module_05_liquidaciones.md §RF-05 -- carga mensual del importe
    (UC-11)."""

    @pytest.mark.asyncio
    async def test_rf05_06_create_charge_entry_for_period(self, client, seed):
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

        response = await client.post(
            f"/v1/recurring-charges/{recurring_charge_id}/entries",
            json={"period": "2026-06", "amount": "12345.67", "notes": "carga junio"},
            headers=owner["headers"],
        )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["recurring_charge_id"] == str(recurring_charge_id)
        assert data["period"] == "2026-06-01"
        assert data["amount"] == "12345.67"
        assert data["notes"] == "carga junio"

    @pytest.mark.asyncio
    async def test_rf05_07_create_charge_entry_for_unknown_charge_returns_404(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )
        fake_charge_id = "00000000-0000-0000-0000-000000000000"

        response = await client.post(
            f"/v1/recurring-charges/{fake_charge_id}/entries",
            json={"period": "2026-06", "amount": "1000.00"},
            headers=owner["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_rf05_08_create_charge_entry_with_future_period_returns_400(self, client, seed):
        """RF-05 §Validaciones: "period: mes valido no futuro"."""
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

        response = await client.post(
            f"/v1/recurring-charges/{recurring_charge_id}/entries",
            json={"period": "2099-01", "amount": "1000.00"},
            headers=owner["headers"],
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_ca_05_08_duplicate_charge_entry_same_period_returns_409(self, client, seed):
        """CA-05-08: "cargar dos veces el mismo concepto+mes devuelve
        `409 CHARGE_ENTRY_ALREADY_EXISTS`"."""
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
        body = {"period": "2026-06", "amount": "1000.00"}

        first = await client.post(
            f"/v1/recurring-charges/{recurring_charge_id}/entries",
            json=body,
            headers=owner["headers"],
        )
        assert first.status_code == 201

        second = await client.post(
            f"/v1/recurring-charges/{recurring_charge_id}/entries",
            json=body,
            headers=owner["headers"],
        )

        assert second.status_code == 409
        assert second.json()["error"]["code"] == "CHARGE_ENTRY_ALREADY_EXISTS"


class TestRN_D04ChargeEntryCorrection:
    """RN-D04: correccion de `charge_entries` siempre auditada (`PATCH
    /charge-entries/:id`)."""

    @pytest.mark.asyncio
    async def test_rn_d04_01_patch_charge_entry_updates_amount_and_audits(self, client, seed):
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
        charge_entry_id = await seed.create_charge_entry_row(
            organization_id=org["organization_id"],
            recurring_charge_id=recurring_charge_id,
            created_by=owner["id"],
            amount="1000.00",
        )

        response = await client.patch(
            f"/v1/charge-entries/{charge_entry_id}",
            json={"amount": "1500.00", "notes": "correccion de importe"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["amount"] == "1500.00"
        assert data["notes"] == "correccion de importe"

        row = await seed.get_charge_entry(charge_entry_id)
        assert row["amount"] == "1500.00"

        audit_entries = await seed.audit_rows(org["organization_id"], "charge_entry.corrected")
        assert len(audit_entries) == 1
        assert audit_entries[0]["entity_id"] == charge_entry_id
        assert audit_entries[0]["before_state"]["amount"] == "1000.00"
        assert audit_entries[0]["after_state"]["amount"] == "1500.00"

    @pytest.mark.asyncio
    async def test_rn_d04_02_patch_charge_entry_unknown_returns_404(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )
        fake_id = "00000000-0000-0000-0000-000000000000"

        response = await client.patch(
            f"/v1/charge-entries/{fake_id}",
            json={"amount": "1500.00"},
            headers=owner["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
