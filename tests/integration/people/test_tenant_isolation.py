"""tests/integration/people/test_tenant_isolation.py

SDD: core/sdd_02_domain_model.md §3 RN-D01 ("Los datos de un tenant nunca
son accesibles desde otro"). Obligatorio segun
docs/skills/module-structure.md checklist / docs/skills/tenant-isolation.md
("Test de aislamiento multi-tenant obligatorio -- GET, LIST, PATCH,
DELETE cross-tenant -> 404").
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


class TestLandlordCrossTenantIsolation:
    async def test_get_landlord_of_another_organization_returns_404_not_403(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        _org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")
        created = await client.post(
            "/v1/landlords",
            json={"name": "De Org B", "commission_pct": "10"},
            headers=owner_b["headers"],
        )
        landlord_b_id = created.json()["data"]["id"]

        response = await client.get(f"/v1/landlords/{landlord_b_id}", headers=owner_a["headers"])

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_list_landlords_never_returns_another_organizations_landlords(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        _org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")
        await client.post(
            "/v1/landlords",
            json={"name": "Solo en B", "commission_pct": "10"},
            headers=owner_b["headers"],
        )

        response = await client.get("/v1/landlords", headers=owner_a["headers"])

        assert response.status_code == 200
        names = {item["name"] for item in response.json()["data"]}
        assert "Solo en B" not in names

    async def test_patch_landlord_of_another_organization_returns_404(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        _org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")
        created = await client.post(
            "/v1/landlords",
            json={"name": "De Org B", "commission_pct": "10"},
            headers=owner_b["headers"],
        )
        landlord_b_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/landlords/{landlord_b_id}",
            json={"phone": "hacked"},
            headers=owner_a["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

        # Confirmar que el recurso de Org B no se modifico.
        untouched = await client.get(f"/v1/landlords/{landlord_b_id}", headers=owner_b["headers"])
        assert untouched.json()["data"]["phone"] is None

    async def test_delete_landlord_of_another_organization_returns_404(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        _org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")
        created = await client.post(
            "/v1/landlords",
            json={"name": "De Org B", "commission_pct": "10"},
            headers=owner_b["headers"],
        )
        landlord_b_id = created.json()["data"]["id"]

        response = await client.delete(f"/v1/landlords/{landlord_b_id}", headers=owner_a["headers"])

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

        # Confirmar que el recurso de Org B sigue existiendo.
        untouched = await client.get(f"/v1/landlords/{landlord_b_id}", headers=owner_b["headers"])
        assert untouched.status_code == 200

    async def test_directly_seeded_landlord_of_another_organization_is_never_visible(
        self, client, seed
    ):
        """Complementario: aislamiento tambien contra un `landlord`
        sembrado directamente en DB (sin pasar por el API de Org B) --
        cubre RLS + filtro explicito, no solo el camino feliz del POST."""
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        org_b, _owner_b = await _seed_org_with_owner(seed, name="Org B")
        landlord_b_id = await seed.create_landlord_row(organization_id=org_b["organization_id"])

        response = await client.get(f"/v1/landlords/{landlord_b_id}", headers=owner_a["headers"])

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"


class TestRenterCrossTenantIsolation:
    async def test_get_renter_of_another_organization_returns_404_not_403(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        _org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")
        created = await client.post(
            "/v1/renters", json={"name": "De Org B"}, headers=owner_b["headers"]
        )
        renter_b_id = created.json()["data"]["id"]

        response = await client.get(f"/v1/renters/{renter_b_id}", headers=owner_a["headers"])

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_list_renters_never_returns_another_organizations_renters(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        _org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")
        await client.post("/v1/renters", json={"name": "Solo en B"}, headers=owner_b["headers"])

        response = await client.get("/v1/renters", headers=owner_a["headers"])

        assert response.status_code == 200
        names = {item["name"] for item in response.json()["data"]}
        assert "Solo en B" not in names

    async def test_patch_renter_of_another_organization_returns_404(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        _org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")
        created = await client.post(
            "/v1/renters", json={"name": "De Org B"}, headers=owner_b["headers"]
        )
        renter_b_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/renters/{renter_b_id}",
            json={"phone": "hacked"},
            headers=owner_a["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_delete_renter_of_another_organization_returns_404(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        _org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")
        created = await client.post(
            "/v1/renters", json={"name": "De Org B"}, headers=owner_b["headers"]
        )
        renter_b_id = created.json()["data"]["id"]

        response = await client.delete(f"/v1/renters/{renter_b_id}", headers=owner_a["headers"])

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

        untouched = await client.get(f"/v1/renters/{renter_b_id}", headers=owner_b["headers"])
        assert untouched.status_code == 200


class TestDebtCertificateCrossTenantIsolation:
    """RN-D01, issue #24: `POST /renters/:id/debt-certificate` cross-tenant."""

    async def test_issue_debt_certificate_of_another_organization_returns_404(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        org_b, _owner_b = await _seed_org_with_owner(seed, name="Org B")
        renter_b_id = await seed.create_renter_row(organization_id=org_b["organization_id"])

        response = await client.post(
            f"/v1/renters/{renter_b_id}/debt-certificate", headers=owner_a["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
