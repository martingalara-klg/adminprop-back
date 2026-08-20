"""tests/integration/maintenance/test_create_work_order.py -- issue #26.

SDD: spec_module_06_mantenimiento.md §RF-01. Covers: CA-06-01.
"""

from __future__ import annotations

import pytest

from tests.integration.maintenance.conftest import TINY_JPEG_BYTES


class TestCA0601CreateWorkOrder:
    """CA-06-01: "Al crear un pedido con pagador y fotos, el encargado
    recibe la notificacion y lo ve en su listado con la direccion de la
    propiedad"."""

    @pytest.mark.asyncio
    async def test_ca_06_01_owner_creates_work_order_with_payer(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"], role_id=org["roles"]["owner"], role_name="owner"
        )
        property_id = await seed.create_property(organization_id=org["organization_id"])

        response = await client.post(
            "/v1/work-orders",
            json={
                "property_id": str(property_id),
                "title": "Arreglar caneria de la cocina",
                "description": "Pierde agua bajo la mesada",
                "payer": "agency",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["payer"] == "agency"
        assert data["status"] == "open"
        assert data["property_address"] == "Av. Test 123"

    @pytest.mark.asyncio
    async def test_ca_06_01_maintenance_user_sees_work_order_in_listing_with_property_address(
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

        create_response = await client.post(
            "/v1/work-orders",
            json={
                "property_id": str(property_id),
                "title": "Arreglar caneria",
                "payer": "landlord",
            },
            headers=owner["headers"],
        )
        assert create_response.status_code == 201
        work_order_id = create_response.json()["data"]["id"]

        list_response = await client.get("/v1/work-orders", headers=maintenance_user["headers"])
        assert list_response.status_code == 200
        items = list_response.json()["data"]
        assert len(items) == 1
        assert items[0]["id"] == work_order_id
        assert items[0]["property_address"] == "Av. Test 123"

    @pytest.mark.asyncio
    async def test_ca_06_01_creating_work_order_notifies_maintenance_role(self, client, seed):
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

        response = await client.post(
            "/v1/work-orders",
            json={"property_id": str(property_id), "title": "Arreglar caneria", "payer": "agency"},
            headers=owner["headers"],
        )
        assert response.status_code == 201

        notifications = await seed.notification_rows(org["organization_id"], "work_order_created")
        assert len(notifications) == 1
        assert notifications[0]["user_id"] == maintenance_user["id"]

    @pytest.mark.asyncio
    async def test_create_work_order_with_unknown_property_returns_404(self, client, seed):
        import uuid

        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"], role_id=org["roles"]["owner"], role_name="owner"
        )

        response = await client.post(
            "/v1/work-orders",
            json={"property_id": str(uuid.uuid4()), "title": "Arreglar caneria", "payer": "agency"},
            headers=owner["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_create_work_order_without_payer_returns_validation_error(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"], role_id=org["roles"]["owner"], role_name="owner"
        )
        property_id = await seed.create_property(organization_id=org["organization_id"])

        response = await client.post(
            "/v1/work-orders",
            json={"property_id": str(property_id), "title": "Arreglar caneria"},
            headers=owner["headers"],
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_create_work_order_with_short_title_returns_validation_error(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"], role_id=org["roles"]["owner"], role_name="owner"
        )
        property_id = await seed.create_property(organization_id=org["organization_id"])

        response = await client.post(
            "/v1/work-orders",
            json={"property_id": str(property_id), "title": "Hi", "payer": "agency"},
            headers=owner["headers"],
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_upload_photo_to_work_order_appears_in_detail(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"], role_id=org["roles"]["owner"], role_name="owner"
        )
        property_id = await seed.create_property(organization_id=org["organization_id"])
        create_response = await client.post(
            "/v1/work-orders",
            json={"property_id": str(property_id), "title": "Arreglar caneria", "payer": "agency"},
            headers=owner["headers"],
        )
        work_order_id = create_response.json()["data"]["id"]

        upload_response = await client.post(
            f"/v1/work-orders/{work_order_id}/attachments",
            files={"file": ("foto.jpg", TINY_JPEG_BYTES, "image/jpeg")},
            headers=owner["headers"],
        )
        assert upload_response.status_code == 201

        detail_response = await client.get(
            f"/v1/work-orders/{work_order_id}", headers=owner["headers"]
        )
        assert detail_response.status_code == 200
        attachments = detail_response.json()["data"]["attachments"]
        assert len(attachments) == 1
        assert attachments[0]["mime_type"] == "image/jpeg"

    @pytest.mark.asyncio
    async def test_upload_unsupported_content_type_returns_validation_error(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"], role_id=org["roles"]["owner"], role_name="owner"
        )
        property_id = await seed.create_property(organization_id=org["organization_id"])
        create_response = await client.post(
            "/v1/work-orders",
            json={"property_id": str(property_id), "title": "Arreglar caneria", "payer": "agency"},
            headers=owner["headers"],
        )
        work_order_id = create_response.json()["data"]["id"]

        upload_response = await client.post(
            f"/v1/work-orders/{work_order_id}/attachments",
            files={"file": ("virus.exe", b"MZ", "application/x-msdownload")},
            headers=owner["headers"],
        )

        assert upload_response.status_code == 400
        assert upload_response.json()["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_download_uploaded_attachment_returns_binary_content(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"], role_id=org["roles"]["owner"], role_name="owner"
        )
        property_id = await seed.create_property(organization_id=org["organization_id"])
        create_response = await client.post(
            "/v1/work-orders",
            json={"property_id": str(property_id), "title": "Arreglar caneria", "payer": "agency"},
            headers=owner["headers"],
        )
        work_order_id = create_response.json()["data"]["id"]
        await client.post(
            f"/v1/work-orders/{work_order_id}/attachments",
            files={"file": ("foto.jpg", TINY_JPEG_BYTES, "image/jpeg")},
            headers=owner["headers"],
        )
        detail_response = await client.get(
            f"/v1/work-orders/{work_order_id}", headers=owner["headers"]
        )
        attachment_id = detail_response.json()["data"]["attachments"][0]["id"]

        download_response = await client.get(
            f"/v1/attachments/{attachment_id}/download", headers=owner["headers"]
        )

        assert download_response.status_code == 200
        assert download_response.content == TINY_JPEG_BYTES
