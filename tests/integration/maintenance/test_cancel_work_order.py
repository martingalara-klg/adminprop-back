"""tests/integration/maintenance/test_cancel_work_order.py -- issue #26.

SDD: spec_module_06_mantenimiento.md §RF-05. Covers: CA-06-07.

CONCERN (ver `modules/maintenance/settlement_hook.py`): `work_orders.
settled_in_settlement_id` (Capa 6, issue #27) todavia no existe, asi que
esta suite prueba el 422 WORK_ORDER_ALREADY_SETTLED via la aproximacion
documentada -- TODO pedido `closed` se trata como "ya liquidado" a
efectos de esta regla, no solo los `payer=agency` efectivamente
liquidados (limitacion conocida, reportada como concern en el PR).
"""

from __future__ import annotations

import pytest


class TestCA0607CancelWorkOrder:
    """CA-06-07: "Un pedido cerrado y ya liquidado no puede cancelarse ni
    reabrirse (`422 WORK_ORDER_ALREADY_SETTLED`)"."""

    @pytest.mark.asyncio
    async def test_ca_06_07_cancelling_a_closed_work_order_returns_422_already_settled(
        self, client, seed
    ):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"], role_id=org["roles"]["owner"], role_name="owner"
        )
        property_id = await seed.create_property(organization_id=org["organization_id"])
        work_order_id = await seed.create_work_order_row(
            organization_id=org["organization_id"],
            property_id=property_id,
            created_by=owner["id"],
            status="closed",
        )

        response = await client.post(
            f"/v1/work-orders/{work_order_id}/cancel",
            json={"reason": "Intento de reabrir un pedido ya cerrado"},
            headers=owner["headers"],
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "WORK_ORDER_ALREADY_SETTLED"

        work_order = await seed.get_work_order(work_order_id)
        assert work_order["status"] == "closed"

    @pytest.mark.asyncio
    async def test_cancel_open_work_order_succeeds_and_is_audited(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"], role_id=org["roles"]["owner"], role_name="owner"
        )
        property_id = await seed.create_property(organization_id=org["organization_id"])
        work_order_id = await seed.create_work_order_row(
            organization_id=org["organization_id"], property_id=property_id, created_by=owner["id"]
        )

        response = await client.post(
            f"/v1/work-orders/{work_order_id}/cancel",
            json={"reason": "El propietario decidio no hacer el arreglo"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        assert response.json()["data"]["status"] == "cancelled"

        audit = await seed.audit_rows(org["organization_id"], "work_order.cancelled")
        assert len(audit) == 1
        assert audit[0]["after_state"]["reason"] == "El propietario decidio no hacer el arreglo"

    @pytest.mark.asyncio
    async def test_cancel_in_progress_work_order_succeeds(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"], role_id=org["roles"]["owner"], role_name="owner"
        )
        property_id = await seed.create_property(organization_id=org["organization_id"])
        work_order_id = await seed.create_work_order_row(
            organization_id=org["organization_id"],
            property_id=property_id,
            created_by=owner["id"],
            status="in_progress",
        )

        response = await client.post(
            f"/v1/work-orders/{work_order_id}/cancel",
            json={"reason": "Se resolvio por otra via"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        assert response.json()["data"]["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_already_cancelled_work_order_returns_422_invalid_status_transition(
        self, client, seed
    ):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"], role_id=org["roles"]["owner"], role_name="owner"
        )
        property_id = await seed.create_property(organization_id=org["organization_id"])
        work_order_id = await seed.create_work_order_row(
            organization_id=org["organization_id"],
            property_id=property_id,
            created_by=owner["id"],
            status="cancelled",
        )

        response = await client.post(
            f"/v1/work-orders/{work_order_id}/cancel",
            json={"reason": "otra vez"},
            headers=owner["headers"],
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_STATUS_TRANSITION"

    @pytest.mark.asyncio
    async def test_cancel_without_reason_returns_validation_error(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"], role_id=org["roles"]["owner"], role_name="owner"
        )
        property_id = await seed.create_property(organization_id=org["organization_id"])
        work_order_id = await seed.create_work_order_row(
            organization_id=org["organization_id"], property_id=property_id, created_by=owner["id"]
        )

        response = await client.post(
            f"/v1/work-orders/{work_order_id}/cancel", json={"reason": ""}, headers=owner["headers"]
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
