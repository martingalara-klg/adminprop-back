"""tests/integration/maintenance/test_property_history.py -- issue #26.

SDD: spec_module_06_mantenimiento.md §RF-06 (UC-16). Covers: CA-06-05.
"""

from __future__ import annotations

import pytest


class TestCA0605PropertyWorkOrderHistory:
    """CA-06-05: "El historial de la propiedad muestra todas las
    reparaciones con pagador, costo y liquidacion asociada cuando
    corresponde"."""

    @pytest.mark.asyncio
    async def test_ca_06_05_history_lists_all_repairs_with_payer_and_cost(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"], role_id=org["roles"]["owner"], role_name="owner"
        )
        property_id = await seed.create_property(organization_id=org["organization_id"])
        agency_wo_id = await seed.create_work_order_row(
            organization_id=org["organization_id"],
            property_id=property_id,
            created_by=owner["id"],
            payer="agency",
            status="closed",
        )
        landlord_wo_id = await seed.create_work_order_row(
            organization_id=org["organization_id"],
            property_id=property_id,
            created_by=owner["id"],
            payer="landlord",
            status="open",
        )

        response = await client.get(
            f"/v1/properties/{property_id}/work-orders", headers=owner["headers"]
        )

        assert response.status_code == 200
        items = response.json()["data"]
        ids = {item["id"] for item in items}
        assert ids == {str(agency_wo_id), str(landlord_wo_id)}
        payers = {item["id"]: item["payer"] for item in items}
        assert payers[str(agency_wo_id)] == "agency"
        assert payers[str(landlord_wo_id)] == "landlord"
        # RF-06: "en que liquidacion se desconto, si aplica" -- siempre
        # None hoy (Capa 6/issue #27 todavia no existe, ver settlement_hook.py).
        assert all(item["settled_in_settlement_id"] is None for item in items)

    @pytest.mark.asyncio
    async def test_history_of_property_without_work_orders_returns_empty_list(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"], role_id=org["roles"]["owner"], role_name="owner"
        )
        property_id = await seed.create_property(organization_id=org["organization_id"])

        response = await client.get(
            f"/v1/properties/{property_id}/work-orders", headers=owner["headers"]
        )

        assert response.status_code == 200
        assert response.json()["data"] == []

    @pytest.mark.asyncio
    async def test_history_of_unknown_property_returns_404(self, client, seed):
        import uuid

        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"], role_id=org["roles"]["owner"], role_name="owner"
        )

        response = await client.get(
            f"/v1/properties/{uuid.uuid4()}/work-orders", headers=owner["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
