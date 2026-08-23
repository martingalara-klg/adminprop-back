"""tests/integration/people/test_renters_crud.py

SDD: docs/sdd/features/spec_module_02_personas.md RF-03 +
core/sdd_03_api_contracts.md §6 "Inquilinos".
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _seed_org_with_owner(seed):
    org = await seed.create_organization_with_system_roles()
    owner = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["owner"],
        role_name="owner",
    )
    return org, owner


class TestRF03CreateAndReadRenter:
    """RF-03: alta con nombre obligatorio, DNI/CUIT, telefono, email, notas."""

    async def test_create_renter_with_minimal_fields(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.post(
            "/v1/renters", json={"name": "Maria Lopez"}, headers=owner["headers"]
        )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["name"] == "Maria Lopez"
        assert data["tax_id"] is None

    async def test_create_renter_with_all_fields(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.post(
            "/v1/renters",
            json={
                "name": "Carlos Diaz",
                "tax_id": "27123456",
                "phone": "351-9876543",
                "email": "carlos@example.com",
                "notes": "Garante: Ana Diaz",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["tax_id"] == "27123456"
        assert data["notes"] == "Garante: Ana Diaz"

    async def test_create_renter_without_name_returns_400(self, client, seed):
        """VALIDATION_ERROR es 400 en este proyecto (ver
        `shared/errors/handlers.py`), no 422."""
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.post("/v1/renters", json={}, headers=owner["headers"])

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_get_unknown_renter_returns_404(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.get(f"/v1/renters/{uuid.uuid4()}", headers=owner["headers"])

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"


class TestRF03UpdateAndDeleteRenter:
    async def test_patch_renter_updates_contact_fields(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        created = await client.post(
            "/v1/renters", json={"name": "Original"}, headers=owner["headers"]
        )
        renter_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/renters/{renter_id}",
            json={"phone": "351-0000000"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        assert response.json()["data"]["phone"] == "351-0000000"
        assert response.json()["data"]["name"] == "Original"

    async def test_patch_renter_updates_name_tax_id_email_notes(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        created = await client.post(
            "/v1/renters", json={"name": "Original"}, headers=owner["headers"]
        )
        renter_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/renters/{renter_id}",
            json={
                "name": "Renombrado",
                "tax_id": "12345678",
                "email": "nuevo@example.com",
                "notes": "Nota actualizada",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["name"] == "Renombrado"
        assert data["tax_id"] == "12345678"
        assert data["email"] == "nuevo@example.com"
        assert data["notes"] == "Nota actualizada"

    async def test_list_renters_paginates_with_cursor(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        await client.post("/v1/renters", json={"name": "Pagina Uno"}, headers=owner["headers"])
        await client.post("/v1/renters", json={"name": "Pagina Dos"}, headers=owner["headers"])

        first_page = await client.get("/v1/renters", params={"limit": 1}, headers=owner["headers"])
        assert first_page.status_code == 200
        assert len(first_page.json()["data"]) == 1
        next_cursor = first_page.json()["meta"]["next_cursor"]
        assert next_cursor is not None

        second_page = await client.get(
            "/v1/renters",
            params={"limit": 1, "cursor": next_cursor},
            headers=owner["headers"],
        )
        assert second_page.status_code == 200
        assert len(second_page.json()["data"]) == 1
        assert first_page.json()["data"][0]["id"] != second_page.json()["data"][0]["id"]

    async def test_delete_renter_without_dependencies_soft_deletes(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        created = await client.post(
            "/v1/renters", json={"name": "A Borrar"}, headers=owner["headers"]
        )
        renter_id = created.json()["data"]["id"]

        delete_response = await client.delete(f"/v1/renters/{renter_id}", headers=owner["headers"])
        assert delete_response.status_code == 204

        get_response = await client.get(f"/v1/renters/{renter_id}", headers=owner["headers"])
        assert get_response.status_code == 404
        assert get_response.json()["error"]["code"] == "NOT_FOUND"
