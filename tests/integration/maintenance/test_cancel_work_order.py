"""tests/integration/maintenance/test_cancel_work_order.py -- issue #26,
actualizado en el issue #29.

SDD: spec_module_06_mantenimiento.md §RF-05. Covers: CA-06-07.

Issue #29 cierra el CONCERN que dejo el #26: `work_orders.
settled_in_settlement_id` (Capa 6, migracion #27) ya esta mapeada y
`settlement_hook.is_work_order_settled` consulta la senal real, ya no la
aproximacion "todo `closed` es `settled`". Esta suite ahora distingue
ambos casos: un `closed` REALMENTE vinculado a una liquidacion (`422
WORK_ORDER_ALREADY_SETTLED`) de un `closed` que todavia no fue liquidado
(`422 INVALID_STATUS_TRANSITION`, sigue sin ser cancelable/reabrible por
ser un estado terminal, pero por una razon distinta).
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
        settlement_id = await seed.create_settlement_row(
            organization_id=org["organization_id"], generated_by=owner["id"]
        )
        work_order_id = await seed.create_work_order_row(
            organization_id=org["organization_id"],
            property_id=property_id,
            created_by=owner["id"],
            status="closed",
            settled_in_settlement_id=str(settlement_id),
        )

        response = await client.post(
            f"/v1/work-orders/{work_order_id}/cancel",
            json={"reason": "Intento de reabrir un pedido ya cerrado y liquidado"},
            headers=owner["headers"],
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "WORK_ORDER_ALREADY_SETTLED"

        work_order = await seed.get_work_order(work_order_id)
        assert work_order["status"] == "closed"

    @pytest.mark.asyncio
    async def test_cancelling_a_closed_but_not_yet_settled_work_order_returns_422_invalid_status_transition(
        self, client, seed
    ):
        """Issue #29 cierra el CONCERN del #26: un pedido `closed` que
        TODAVIA no fue incluido en ninguna liquidacion ya no se rechaza
        con `WORK_ORDER_ALREADY_SETTLED` -- sigue siendo un estado
        terminal (no cancelable/reabrible), pero por
        `INVALID_STATUS_TRANSITION`, no por la regla RN-L04."""
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
            json={"reason": "Intento de reabrir un pedido cerrado sin liquidar"},
            headers=owner["headers"],
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_STATUS_TRANSITION"

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
