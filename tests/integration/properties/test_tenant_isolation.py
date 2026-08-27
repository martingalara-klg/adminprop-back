"""tests/integration/properties/test_tenant_isolation.py

SDD: core/sdd_02_domain_model.md §3 RN-D01 ("Los datos de un tenant nunca
son accesibles desde otro"). Obligatorio segun
docs/skills/tenant-isolation.md ("Test de aislamiento multi-tenant
obligatorio -- GET, LIST, PATCH, DELETE cross-tenant -> 404").
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def _seed_org_with_owner(seed, *, name: str):
    org = await seed.create_organization_with_system_roles(name=name)
    owner = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["owner"],
        role_name="owner",
    )
    return org, owner


class TestPropertyCrossTenantIsolation:
    async def test_get_property_of_another_organization_returns_404_not_403(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        _org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")
        landlord_b = await seed.create_landlord_row(organization_id=owner_b["organization_id"])
        neighborhood_b = await seed.create_neighborhood_row(
            organization_id=owner_b["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "De Org B",
                "landlord_id": str(landlord_b),
                "neighborhood_id": str(neighborhood_b),
            },
            headers=owner_b["headers"],
        )
        property_b_id = created.json()["data"]["id"]

        response = await client.get(f"/v1/properties/{property_b_id}", headers=owner_a["headers"])

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_list_properties_never_returns_another_organizations_properties(
        self, client, seed
    ):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        _org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")
        landlord_b = await seed.create_landlord_row(organization_id=owner_b["organization_id"])
        neighborhood_b = await seed.create_neighborhood_row(
            organization_id=owner_b["organization_id"]
        )
        await client.post(
            "/v1/properties",
            json={
                "address": "Solo en B",
                "landlord_id": str(landlord_b),
                "neighborhood_id": str(neighborhood_b),
            },
            headers=owner_b["headers"],
        )

        response = await client.get("/v1/properties", headers=owner_a["headers"])

        assert response.status_code == 200
        addresses = {item["address"] for item in response.json()["data"]}
        assert "Solo en B" not in addresses

    async def test_patch_property_of_another_organization_returns_404(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        _org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")
        landlord_b = await seed.create_landlord_row(organization_id=owner_b["organization_id"])
        neighborhood_b = await seed.create_neighborhood_row(
            organization_id=owner_b["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "De Org B",
                "landlord_id": str(landlord_b),
                "neighborhood_id": str(neighborhood_b),
            },
            headers=owner_b["headers"],
        )
        property_b_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/properties/{property_b_id}",
            json={"address": "hacked"},
            headers=owner_a["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

        untouched = await client.get(f"/v1/properties/{property_b_id}", headers=owner_b["headers"])
        assert untouched.json()["data"]["address"] == "De Org B"

    async def test_delete_property_of_another_organization_returns_404(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        _org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")
        landlord_b = await seed.create_landlord_row(organization_id=owner_b["organization_id"])
        neighborhood_b = await seed.create_neighborhood_row(
            organization_id=owner_b["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "De Org B",
                "landlord_id": str(landlord_b),
                "neighborhood_id": str(neighborhood_b),
            },
            headers=owner_b["headers"],
        )
        property_b_id = created.json()["data"]["id"]

        response = await client.delete(
            f"/v1/properties/{property_b_id}", headers=owner_a["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

        untouched = await client.get(f"/v1/properties/{property_b_id}", headers=owner_b["headers"])
        assert untouched.status_code == 200

    async def test_directly_seeded_property_of_another_organization_is_never_visible(
        self, client, seed
    ):
        """Complementario: aislamiento tambien contra una `property`
        sembrada directamente en DB (sin pasar por el API de Org B) --
        cubre RLS + filtro explicito, no solo el camino feliz del POST."""
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        org_b, _owner_b = await _seed_org_with_owner(seed, name="Org B")
        landlord_b = await seed.create_landlord_row(organization_id=org_b["organization_id"])
        property_b_id = await seed.create_property_row(
            organization_id=org_b["organization_id"], landlord_id=landlord_b
        )

        response = await client.get(f"/v1/properties/{property_b_id}", headers=owner_a["headers"])

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_create_property_with_landlord_of_another_organization_returns_404(
        self, client, seed
    ):
        """RN-D01 aplicada a `landlord_id`: un `landlord_id` valido pero de
        otra organizacion se trata igual que "no existe"."""
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        org_b, _owner_b = await _seed_org_with_owner(seed, name="Org B")
        landlord_b = await seed.create_landlord_row(organization_id=org_b["organization_id"])
        neighborhood_a = await seed.create_neighborhood_row(
            organization_id=owner_a["organization_id"]
        )

        response = await client.post(
            "/v1/properties",
            json={
                "address": "Intento cross-tenant",
                "landlord_id": str(landlord_b),
                "neighborhood_id": str(neighborhood_a),
            },
            headers=owner_a["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_create_property_with_neighborhood_of_another_organization_returns_404(
        self, client, seed
    ):
        """RN-D01 aplicada a `neighborhood_id` (issue #99): mismo criterio
        que `landlord_id`."""
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        org_b, _owner_b = await _seed_org_with_owner(seed, name="Org B")
        landlord_a = await seed.create_landlord_row(organization_id=owner_a["organization_id"])
        neighborhood_b = await seed.create_neighborhood_row(
            organization_id=org_b["organization_id"]
        )

        response = await client.post(
            "/v1/properties",
            json={
                "address": "Intento cross-tenant barrio",
                "landlord_id": str(landlord_a),
                "neighborhood_id": str(neighborhood_b),
            },
            headers=owner_a["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
        assert response.json()["error"]["field"] == "neighborhood_id"


class TestServiceAccountCrossTenantIsolation:
    async def test_service_accounts_of_another_organizations_property_are_not_reachable(
        self, client, seed
    ):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        _org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")
        landlord_b = await seed.create_landlord_row(organization_id=owner_b["organization_id"])
        neighborhood_b = await seed.create_neighborhood_row(
            organization_id=owner_b["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "De Org B con cuentas",
                "landlord_id": str(landlord_b),
                "neighborhood_id": str(neighborhood_b),
            },
            headers=owner_b["headers"],
        )
        property_b_id = created.json()["data"]["id"]

        response = await client.get(
            f"/v1/properties/{property_b_id}/service-accounts", headers=owner_a["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_patch_service_account_of_another_organization_returns_404(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        _org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")
        landlord_b = await seed.create_landlord_row(organization_id=owner_b["organization_id"])
        neighborhood_b = await seed.create_neighborhood_row(
            organization_id=owner_b["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "De Org B",
                "landlord_id": str(landlord_b),
                "neighborhood_id": str(neighborhood_b),
            },
            headers=owner_b["headers"],
        )
        property_b_id = created.json()["data"]["id"]
        account = await client.post(
            f"/v1/properties/{property_b_id}/service-accounts",
            json={"service_type": "gas", "account_number": "GAS-B"},
            headers=owner_b["headers"],
        )
        account_id = account.json()["data"]["id"]

        response = await client.patch(
            f"/v1/service-accounts/{account_id}",
            json={"account_number": "hacked"},
            headers=owner_a["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"


class TestNeighborhoodCrossTenantIsolation:
    """Issue #99: RN-D01 aplicada al catalogo de barrios."""

    async def test_get_neighborhood_of_another_organization_is_not_listed(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        org_b, _owner_b = await _seed_org_with_owner(seed, name="Org B")
        await seed.create_neighborhood_row(
            organization_id=org_b["organization_id"], name="Solo en B"
        )

        response = await client.get("/v1/neighborhoods", headers=owner_a["headers"])

        assert response.status_code == 200
        names = {item["name"] for item in response.json()["data"]}
        assert "Solo en B" not in names

    async def test_patch_neighborhood_of_another_organization_returns_404(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        org_b, _owner_b = await _seed_org_with_owner(seed, name="Org B")
        neighborhood_b = await seed.create_neighborhood_row(organization_id=org_b["organization_id"])

        response = await client.patch(
            f"/v1/neighborhoods/{neighborhood_b}",
            json={"name": "hacked"},
            headers=owner_a["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_delete_neighborhood_of_another_organization_returns_404(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        org_b, _owner_b = await _seed_org_with_owner(seed, name="Org B")
        neighborhood_b = await seed.create_neighborhood_row(organization_id=org_b["organization_id"])

        response = await client.delete(
            f"/v1/neighborhoods/{neighborhood_b}", headers=owner_a["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
