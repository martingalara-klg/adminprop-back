"""tests/integration/maintenance/test_quotes.py -- issue #26.

SDD: spec_module_06_mantenimiento.md §RF-02. Covers: CA-06-02.
"""

from __future__ import annotations

import pytest

from tests.integration.maintenance.conftest import TINY_JPEG_BYTES


async def _seed_open_work_order(seed):
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
    )
    return org, owner, admin, maintenance_user, work_order_id


class TestCA0602SubmitQuotes:
    """CA-06-02: "El encargado sube dos cotizaciones con fotos; owner y
    admin reciben notificacion por cada una"."""

    @pytest.mark.asyncio
    async def test_ca_06_02_maintenance_submits_two_quotes_with_photos_notifies_owner_and_admin(
        self, client, seed
    ):
        org, owner, admin, maintenance_user, work_order_id = await _seed_open_work_order(seed)

        quote_1 = await client.post(
            f"/v1/work-orders/{work_order_id}/quotes",
            json={"amount": "1500.00", "description": "Presupuesto plomero A"},
            headers=maintenance_user["headers"],
        )
        assert quote_1.status_code == 201
        quote_1_id = quote_1.json()["data"]["id"]
        photo_1 = await client.post(
            f"/v1/quotes/{quote_1_id}/attachments",
            files={"file": ("foto1.jpg", TINY_JPEG_BYTES, "image/jpeg")},
            headers=maintenance_user["headers"],
        )
        assert photo_1.status_code == 201

        quote_2 = await client.post(
            f"/v1/work-orders/{work_order_id}/quotes",
            json={"amount": "1800.00", "description": "Presupuesto plomero B"},
            headers=maintenance_user["headers"],
        )
        assert quote_2.status_code == 201
        quote_2_id = quote_2.json()["data"]["id"]
        photo_2 = await client.post(
            f"/v1/quotes/{quote_2_id}/attachments",
            files={"file": ("foto2.jpg", TINY_JPEG_BYTES, "image/jpeg")},
            headers=maintenance_user["headers"],
        )
        assert photo_2.status_code == 201

        detail = await client.get(f"/v1/work-orders/{work_order_id}", headers=owner["headers"])
        quotes = detail.json()["data"]["quotes"]
        assert {q["id"] for q in quotes} == {quote_1_id, quote_2_id}

        notifications = await seed.notification_rows(org["organization_id"], "quote_submitted")
        assert len(notifications) == 4  # 2 cotizaciones x (owner + admin)
        recipients = {n["user_id"] for n in notifications}
        assert recipients == {owner["id"], admin["id"]}

    @pytest.mark.asyncio
    async def test_admin_can_also_submit_a_quote(self, client, seed):
        _org, _owner, admin, _maintenance_user, work_order_id = await _seed_open_work_order(seed)

        response = await client.post(
            f"/v1/work-orders/{work_order_id}/quotes",
            json={"amount": "1200.00"},
            headers=admin["headers"],
        )

        assert response.status_code == 201

    @pytest.mark.asyncio
    async def test_quote_with_negative_amount_returns_validation_error(self, client, seed):
        _org, _owner, _admin, maintenance_user, work_order_id = await _seed_open_work_order(seed)

        response = await client.post(
            f"/v1/work-orders/{work_order_id}/quotes",
            json={"amount": "-10.00"},
            headers=maintenance_user["headers"],
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_quote_on_in_progress_work_order_returns_invalid_status_transition(
        self, client, seed
    ):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"], role_id=org["roles"]["owner"], role_name="owner"
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
            status="in_progress",
        )

        response = await client.post(
            f"/v1/work-orders/{work_order_id}/quotes",
            json={"amount": "500.00"},
            headers=maintenance_user["headers"],
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_STATUS_TRANSITION"

    @pytest.mark.asyncio
    async def test_quote_on_unknown_work_order_returns_404(self, client, seed):
        import uuid

        org = await seed.create_organization_with_system_roles()
        maintenance_user = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["maintenance"],
            role_name="maintenance",
        )

        response = await client.post(
            f"/v1/work-orders/{uuid.uuid4()}/quotes",
            json={"amount": "500.00"},
            headers=maintenance_user["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
