"""tests/integration/people/test_landlords_crud.py

SDD: docs/sdd/features/spec_module_02_personas.md RF-01/RF-02 +
core/sdd_03_api_contracts.md §5 "Propietarios".
Implements: CA-02-01.
"""

from __future__ import annotations

from decimal import Decimal

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


class TestCA0201CreateLandlordWithCommissionPct:
    """CA-02-01: Se crea un propietario con % de comision; el % queda
    disponible para las liquidaciones de todas sus propiedades."""

    async def test_ca_02_01_create_landlord_persists_commission_pct(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.post(
            "/v1/landlords",
            json={
                "name": "Juan Perez",
                "tax_id": "20-12345678-6",
                "phone": "351-1234567",
                "email": "juan@example.com",
                "bank_info": "CBU 2850590940090418135201",
                "commission_pct": "8.50",
                "notes": "Cliente historico",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["name"] == "Juan Perez"
        # commission_pct persiste como NUMERIC(14,4) -- comparar por
        # valor decimal, no por string (la DB serializa "8.5000").
        assert Decimal(data["commission_pct"]) == Decimal("8.50")
        assert data["tax_id"] == "20123456786"
        assert data["bank_info"] == "CBU 2850590940090418135201"

    async def test_ca_02_01_create_landlord_without_commission_pct_returns_400(self, client, seed):
        """RF-01: `commission_pct` es obligatorio -- "sin el no se puede
        liquidar" -- Pydantic lo rechaza con VALIDATION_ERROR (400 via el
        handler global de `RequestValidationError`, ver
        `shared/errors/handlers.py`: VALIDATION_ERROR es 400 en este
        proyecto, no 422)."""
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.post(
            "/v1/landlords",
            json={"name": "Sin Comision"},
            headers=owner["headers"],
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_ca_02_01_create_landlord_commission_pct_out_of_range_returns_400(
        self, client, seed
    ):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.post(
            "/v1/landlords",
            json={"name": "Comision Invalida", "commission_pct": "150"},
            headers=owner["headers"],
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


class TestLandlordGetListDelete:
    """Flujo CRUD basico (RF-01/RF-02), complementario a CA-02-01."""

    async def test_get_landlord_returns_full_detail(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        created = await client.post(
            "/v1/landlords",
            json={"name": "Ficha Completa", "commission_pct": "10"},
            headers=owner["headers"],
        )
        landlord_id = created.json()["data"]["id"]

        response = await client.get(f"/v1/landlords/{landlord_id}", headers=owner["headers"])

        assert response.status_code == 200
        assert response.json()["data"]["id"] == landlord_id

    async def test_get_unknown_landlord_returns_404(self, client, seed):
        import uuid

        _org, owner = await _seed_org_with_owner(seed)

        response = await client.get(f"/v1/landlords/{uuid.uuid4()}", headers=owner["headers"])

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_list_landlords_returns_created_landlord(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        await client.post(
            "/v1/landlords",
            json={"name": "Listado Uno", "commission_pct": "5"},
            headers=owner["headers"],
        )

        response = await client.get("/v1/landlords", headers=owner["headers"])

        assert response.status_code == 200
        names = {item["name"] for item in response.json()["data"]}
        assert "Listado Uno" in names
        assert response.json()["meta"]["limit"] == 20

    async def test_delete_landlord_without_dependencies_soft_deletes(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        created = await client.post(
            "/v1/landlords",
            json={"name": "A Borrar", "commission_pct": "5"},
            headers=owner["headers"],
        )
        landlord_id = created.json()["data"]["id"]

        delete_response = await client.delete(
            f"/v1/landlords/{landlord_id}", headers=owner["headers"]
        )
        assert delete_response.status_code == 204

        get_response = await client.get(f"/v1/landlords/{landlord_id}", headers=owner["headers"])
        assert get_response.status_code == 404
        assert get_response.json()["error"]["code"] == "NOT_FOUND"

    async def test_list_landlords_paginates_with_cursor(self, client, seed):
        """RF-02: paginacion cursor-based -- `limit=1` con 2 propietarios
        obliga a usar `meta.next_cursor` para traer el segundo."""
        _org, owner = await _seed_org_with_owner(seed)
        await client.post(
            "/v1/landlords",
            json={"name": "Pagina Uno", "commission_pct": "5"},
            headers=owner["headers"],
        )
        await client.post(
            "/v1/landlords",
            json={"name": "Pagina Dos", "commission_pct": "5"},
            headers=owner["headers"],
        )

        first_page = await client.get(
            "/v1/landlords", params={"limit": 1}, headers=owner["headers"]
        )
        assert first_page.status_code == 200
        assert len(first_page.json()["data"]) == 1
        next_cursor = first_page.json()["meta"]["next_cursor"]
        assert next_cursor is not None

        second_page = await client.get(
            "/v1/landlords",
            params={"limit": 1, "cursor": next_cursor},
            headers=owner["headers"],
        )
        assert second_page.status_code == 200
        assert len(second_page.json()["data"]) == 1
        assert first_page.json()["data"][0]["id"] != second_page.json()["data"][0]["id"]

    async def test_patch_landlord_updates_name_and_tax_id(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        created = await client.post(
            "/v1/landlords",
            json={"name": "Nombre Viejo", "commission_pct": "10"},
            headers=owner["headers"],
        )
        landlord_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/landlords/{landlord_id}",
            json={"name": "Nombre Nuevo", "tax_id": "20-12345678-6"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["name"] == "Nombre Nuevo"
        assert data["tax_id"] == "20123456786"

    async def test_patch_landlord_updates_bank_info(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        created = await client.post(
            "/v1/landlords",
            json={"name": "Con Banco", "commission_pct": "10"},
            headers=owner["headers"],
        )
        landlord_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/landlords/{landlord_id}",
            json={"bank_info": "Nuevo CBU 111"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        assert response.json()["data"]["bank_info"] == "Nuevo CBU 111"
