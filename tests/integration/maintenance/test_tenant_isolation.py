"""tests/integration/maintenance/test_tenant_isolation.py -- issue #26.

Invariante: RN-D01 -- los datos de un tenant nunca son accesibles desde
otro tenant. Cubre `GET/POST /work-orders[/:id]`, `POST .../quotes`,
`POST /quotes/:id/approve`, `POST /work-orders/:id/close|cancel`,
`GET /attachments/:id/download` y `GET /properties/:id/work-orders`.
"""

from __future__ import annotations

import pytest

from tests.integration.maintenance.conftest import TINY_JPEG_BYTES


async def _seed_org_with_work_order(seed):
    org = await seed.create_organization_with_system_roles()
    owner = await seed.add_member(
        organization_id=org["organization_id"], role_id=org["roles"]["owner"], role_name="owner"
    )
    property_id = await seed.create_property(organization_id=org["organization_id"])
    work_order_id = await seed.create_work_order_row(
        organization_id=org["organization_id"], property_id=property_id, created_by=owner["id"]
    )
    return org, owner, property_id, work_order_id


class TestTenantIsolation:
    """RN-D01 enforcement: tenant A no accede a recursos del tenant B."""

    @pytest.mark.asyncio
    async def test_get_work_order_of_other_tenant_returns_404(self, client, seed):
        org_a = await seed.create_organization_with_system_roles()
        owner_a = await seed.add_member(
            organization_id=org_a["organization_id"],
            role_id=org_a["roles"]["owner"],
            role_name="owner",
        )
        _org_b, _owner_b, _property_b, work_order_b_id = await _seed_org_with_work_order(seed)

        response = await client.get(
            f"/v1/work-orders/{work_order_b_id}", headers=owner_a["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_list_work_orders_never_returns_another_tenants_orders(self, client, seed):
        org_a = await seed.create_organization_with_system_roles()
        owner_a = await seed.add_member(
            organization_id=org_a["organization_id"],
            role_id=org_a["roles"]["owner"],
            role_name="owner",
        )
        _org_b, _owner_b, _property_b, work_order_b_id = await _seed_org_with_work_order(seed)

        response = await client.get("/v1/work-orders", headers=owner_a["headers"])

        assert response.status_code == 200
        ids = {item["id"] for item in response.json()["data"]}
        assert str(work_order_b_id) not in ids

    @pytest.mark.asyncio
    async def test_create_work_order_for_other_tenant_property_returns_404(self, client, seed):
        org_a = await seed.create_organization_with_system_roles()
        owner_a = await seed.add_member(
            organization_id=org_a["organization_id"],
            role_id=org_a["roles"]["owner"],
            role_name="owner",
        )
        org_b = await seed.create_organization_with_system_roles()
        property_b_id = await seed.create_property(organization_id=org_b["organization_id"])

        response = await client.post(
            "/v1/work-orders",
            json={
                "property_id": str(property_b_id),
                "title": "Arreglo cross-tenant",
                "payer": "agency",
            },
            headers=owner_a["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_quote_on_other_tenant_work_order_returns_404(self, client, seed):
        org_a = await seed.create_organization_with_system_roles()
        maintenance_a = await seed.add_member(
            organization_id=org_a["organization_id"],
            role_id=org_a["roles"]["maintenance"],
            role_name="maintenance",
        )
        _org_b, _owner_b, _property_b, work_order_b_id = await _seed_org_with_work_order(seed)

        response = await client.post(
            f"/v1/work-orders/{work_order_b_id}/quotes",
            json={"amount": "500.00"},
            headers=maintenance_a["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_approve_other_tenant_quote_returns_404(self, client, seed):
        org_a = await seed.create_organization_with_system_roles()
        owner_a = await seed.add_member(
            organization_id=org_a["organization_id"],
            role_id=org_a["roles"]["owner"],
            role_name="owner",
        )
        org_b, owner_b, _property_b, work_order_b_id = await _seed_org_with_work_order(seed)
        quote_b_id = await seed.create_quote_row(
            organization_id=org_b["organization_id"],
            work_order_id=work_order_b_id,
            submitted_by=owner_b["id"],
        )

        response = await client.post(f"/v1/quotes/{quote_b_id}/approve", headers=owner_a["headers"])

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

        quote_b = await seed.get_quote(quote_b_id)
        assert quote_b["status"] == "submitted"

    @pytest.mark.asyncio
    async def test_close_other_tenant_work_order_returns_404(self, client, seed):
        org_a = await seed.create_organization_with_system_roles()
        owner_a = await seed.add_member(
            organization_id=org_a["organization_id"],
            role_id=org_a["roles"]["owner"],
            role_name="owner",
        )
        _org_b, _owner_b, _property_b, work_order_b_id = await _seed_org_with_work_order(seed)

        response = await client.post(
            f"/v1/work-orders/{work_order_b_id}/close",
            json={"final_cost": "1.00"},
            headers=owner_a["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_cancel_other_tenant_work_order_returns_404(self, client, seed):
        org_a = await seed.create_organization_with_system_roles()
        owner_a = await seed.add_member(
            organization_id=org_a["organization_id"],
            role_id=org_a["roles"]["owner"],
            role_name="owner",
        )
        _org_b, _owner_b, _property_b, work_order_b_id = await _seed_org_with_work_order(seed)

        response = await client.post(
            f"/v1/work-orders/{work_order_b_id}/cancel",
            json={"reason": "cross-tenant"},
            headers=owner_a["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_download_other_tenant_attachment_returns_404(self, client, seed):
        org_a = await seed.create_organization_with_system_roles()
        owner_a = await seed.add_member(
            organization_id=org_a["organization_id"],
            role_id=org_a["roles"]["owner"],
            role_name="owner",
        )
        _org_b, owner_b, _property_b, work_order_b_id = await _seed_org_with_work_order(seed)
        upload_response = await client.post(
            f"/v1/work-orders/{work_order_b_id}/attachments",
            files={"file": ("foto.jpg", TINY_JPEG_BYTES, "image/jpeg")},
            headers=owner_b["headers"],
        )
        assert upload_response.status_code == 201
        detail = await client.get(f"/v1/work-orders/{work_order_b_id}", headers=owner_b["headers"])
        attachment_b_id = detail.json()["data"]["attachments"][0]["id"]

        response = await client.get(
            f"/v1/attachments/{attachment_b_id}/download", headers=owner_a["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_property_history_of_other_tenant_property_returns_404(self, client, seed):
        org_a = await seed.create_organization_with_system_roles()
        owner_a = await seed.add_member(
            organization_id=org_a["organization_id"],
            role_id=org_a["roles"]["owner"],
            role_name="owner",
        )
        _org_b, _owner_b, property_b_id, _work_order_b_id = await _seed_org_with_work_order(seed)

        response = await client.get(
            f"/v1/properties/{property_b_id}/work-orders", headers=owner_a["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
