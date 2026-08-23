"""tests/integration/maintenance/test_close_work_order.py -- issue #26.

SDD: spec_module_06_mantenimiento.md §RF-04. Covers: CA-06-04.

Alcance conocido (ver `modules/maintenance/settlement_hook.py`): el
"costo pendiente de liquidar" para `payer=agency` no tiene todavia una
columna propia (`settled_in_settlement_id` es Capa 6, issue #27) -- esta
suite verifica que `payer`/`final_cost` queden persistidos correctamente
para AMBOS payers (la base que el modulo de liquidaciones futuro
consumira), no un estado de liquidacion que todavia no existe.
"""

from __future__ import annotations

import pytest

from tests.integration.maintenance.conftest import TINY_JPEG_BYTES


async def _seed_work_order_with_approved_quote(seed, *, payer: str = "agency"):
    org = await seed.create_organization_with_system_roles()
    owner = await seed.add_member(
        organization_id=org["organization_id"], role_id=org["roles"]["owner"], role_name="owner"
    )
    admin = await seed.add_member(
        organization_id=org["organization_id"], role_id=org["roles"]["admin"], role_name="admin"
    )
    maintenance_user = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["maintenance"],
        role_name="maintenance",
    )
    property_id = await seed.create_property(organization_id=org["organization_id"])
    work_order_id = await seed.create_work_order_row(
        organization_id=org["organization_id"],
        property_id=property_id,
        created_by=owner["id"],
        payer=payer,
        status="in_progress",
    )
    approved_quote_id = await seed.create_quote_row(
        organization_id=org["organization_id"],
        work_order_id=work_order_id,
        submitted_by=maintenance_user["id"],
        amount="2000.00",
        status="approved",
    )
    # RF-03: en el flujo real `WorkOrderQuoteService.approve` setea esta FK;
    # como este helper siembra la cotizacion `approved` directo en DB
    # (sin pasar por el endpoint de aprobacion), hay que wirearla a mano.
    await seed.set_approved_quote(work_order_id=work_order_id, quote_id=approved_quote_id)
    return org, owner, admin, maintenance_user, work_order_id, approved_quote_id


class TestCA0604CloseWorkOrder:
    """CA-06-04: "Al cerrar el trabajo con fotos y costo final, owner y
    admin son notificados; con payer=agency el costo aparece como
    pendiente de liquidar; con payer=landlord solo queda en el
    historial"."""

    @pytest.mark.asyncio
    async def test_ca_06_04_close_with_agency_payer_uses_final_cost_and_notifies_owner_admin(
        self, client, seed
    ):
        (
            org,
            owner,
            admin,
            maintenance_user,
            work_order_id,
            _quote_id,
        ) = await _seed_work_order_with_approved_quote(seed, payer="agency")
        photo = await client.post(
            f"/v1/work-orders/{work_order_id}/attachments",
            files={"file": ("cierre.jpg", TINY_JPEG_BYTES, "image/jpeg")},
            headers=maintenance_user["headers"],
        )
        assert photo.status_code == 201

        response = await client.post(
            f"/v1/work-orders/{work_order_id}/close",
            json={"final_cost": "2200.00"},
            headers=maintenance_user["headers"],
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "closed"
        assert data["final_cost"] == "2200.00"
        assert data["payer"] == "agency"

        # `POST .../close` responde WorkOrderResponse (WorkOrderSummary) --
        # el detalle con adjuntos se confirma via GET (RF-02/RF-04).
        detail_response = await client.get(
            f"/v1/work-orders/{work_order_id}", headers=maintenance_user["headers"]
        )
        assert len(detail_response.json()["data"]["attachments"]) == 1

        notifications = await seed.notification_rows(org["organization_id"], "work_order_closed")
        recipients = {n["user_id"] for n in notifications}
        assert recipients == {owner["id"], admin["id"]}

    @pytest.mark.asyncio
    async def test_ca_06_04_close_with_landlord_payer_stays_history_only(self, client, seed):
        (
            _org,
            _owner,
            _admin,
            maintenance_user,
            work_order_id,
            _quote_id,
        ) = await _seed_work_order_with_approved_quote(seed, payer="landlord")

        response = await client.post(
            f"/v1/work-orders/{work_order_id}/close",
            json={},
            headers=maintenance_user["headers"],
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["payer"] == "landlord"
        assert data["status"] == "closed"
        # RF-04: sin final_cost explicito, toma el de la cotizacion aprobada.
        assert data["final_cost"] == "2000.00"

    @pytest.mark.asyncio
    async def test_close_already_closed_work_order_returns_409(self, client, seed):
        (
            _org,
            _owner,
            _admin,
            maintenance_user,
            work_order_id,
            _quote_id,
        ) = await _seed_work_order_with_approved_quote(seed)
        first = await client.post(
            f"/v1/work-orders/{work_order_id}/close", json={}, headers=maintenance_user["headers"]
        )
        assert first.status_code == 200

        second = await client.post(
            f"/v1/work-orders/{work_order_id}/close", json={}, headers=maintenance_user["headers"]
        )

        assert second.status_code == 409
        assert second.json()["error"]["code"] == "WORK_ORDER_ALREADY_CLOSED"

    @pytest.mark.asyncio
    async def test_admin_can_close_work_order(self, client, seed):
        (
            _org,
            _owner,
            admin,
            _maintenance_user,
            work_order_id,
            _quote_id,
        ) = await _seed_work_order_with_approved_quote(seed)

        response = await client.post(
            f"/v1/work-orders/{work_order_id}/close", json={}, headers=admin["headers"]
        )

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_close_open_work_order_without_final_cost_or_approved_quote_returns_validation_error(
        self, client, seed
    ):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"], role_id=org["roles"]["owner"], role_name="owner"
        )
        property_id = await seed.create_property(organization_id=org["organization_id"])
        work_order_id = await seed.create_work_order_row(
            organization_id=org["organization_id"], property_id=property_id, created_by=owner["id"]
        )

        response = await client.post(
            f"/v1/work-orders/{work_order_id}/close", json={}, headers=owner["headers"]
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
