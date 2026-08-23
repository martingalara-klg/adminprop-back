"""tests/integration/contracts/test_tenant_isolation.py

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


async def _seed_property_and_renter(seed, organization_id):
    landlord_id = await seed.create_landlord_row(organization_id=organization_id)
    property_id = await seed.create_property_row(
        organization_id=organization_id, landlord_id=landlord_id
    )
    renter_id = await seed.create_renter_row(organization_id=organization_id)
    return property_id, renter_id


class TestContractCrossTenantIsolation:
    async def test_get_contract_of_another_organization_returns_404_not_403(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        _org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")
        property_b, renter_b = await _seed_property_and_renter(seed, owner_b["organization_id"])
        contract_b_id = await seed.create_contract_row(
            organization_id=owner_b["organization_id"],
            property_id=property_b,
            renter_id=renter_b,
        )

        response = await client.get(f"/v1/contracts/{contract_b_id}", headers=owner_a["headers"])

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_list_contracts_never_returns_another_organizations_contracts(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        _org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")
        property_b, renter_b = await _seed_property_and_renter(seed, owner_b["organization_id"])
        contract_b_id = await seed.create_contract_row(
            organization_id=owner_b["organization_id"],
            property_id=property_b,
            renter_id=renter_b,
        )

        response = await client.get("/v1/contracts", headers=owner_a["headers"])

        assert response.status_code == 200
        ids = {item["id"] for item in response.json()["data"]}
        assert str(contract_b_id) not in ids

    async def test_patch_contract_of_another_organization_returns_404(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        _org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")
        property_b, renter_b = await _seed_property_and_renter(seed, owner_b["organization_id"])
        contract_b_id = await seed.create_contract_row(
            organization_id=owner_b["organization_id"],
            property_id=property_b,
            renter_id=renter_b,
        )

        response = await client.patch(
            f"/v1/contracts/{contract_b_id}",
            json={"notes": "hacked"},
            headers=owner_a["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_activate_contract_of_another_organization_returns_404(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        _org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")
        property_b, renter_b = await _seed_property_and_renter(seed, owner_b["organization_id"])
        contract_b_id = await seed.create_contract_row(
            organization_id=owner_b["organization_id"],
            property_id=property_b,
            renter_id=renter_b,
            status="draft",
        )

        response = await client.post(
            f"/v1/contracts/{contract_b_id}/activate", headers=owner_a["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_terminate_contract_of_another_organization_returns_404(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        _org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")
        property_b, renter_b = await _seed_property_and_renter(seed, owner_b["organization_id"])
        contract_b_id = await seed.create_contract_row(
            organization_id=owner_b["organization_id"],
            property_id=property_b,
            renter_id=renter_b,
            status="active",
        )

        response = await client.post(
            f"/v1/contracts/{contract_b_id}/terminate",
            json={"reason": "Intento cross-tenant"},
            headers=owner_a["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_create_contract_with_property_of_another_organization_returns_404(
        self, client, seed
    ):
        """RN-06/RN-D01 aplicada a `property_id`: valido pero de otra
        organizacion se trata igual que "no existe"."""
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        org_b, _owner_b = await _seed_org_with_owner(seed, name="Org B")
        property_b, _renter_b = await _seed_property_and_renter(seed, org_b["organization_id"])
        renter_a = await seed.create_renter_row(organization_id=owner_a["organization_id"])

        response = await client.post(
            "/v1/contracts",
            json={
                "property_id": str(property_b),
                "renter_id": str(renter_a),
                "currency": "ARS",
                "initial_amount": "1000.00",
                "start_date": "2026-01-01",
                "end_date": "2027-01-01",
                "daily_late_fee_pct": "0.1",
            },
            headers=owner_a["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
        assert response.json()["error"]["field"] == "property_id"

    async def test_create_contract_with_renter_of_another_organization_returns_404(
        self, client, seed
    ):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        org_b, _owner_b = await _seed_org_with_owner(seed, name="Org B")
        renter_b = await seed.create_renter_row(organization_id=org_b["organization_id"])
        property_a, _renter_a = await _seed_property_and_renter(seed, owner_a["organization_id"])

        response = await client.post(
            "/v1/contracts",
            json={
                "property_id": str(property_a),
                "renter_id": str(renter_b),
                "currency": "ARS",
                "initial_amount": "1000.00",
                "start_date": "2026-01-01",
                "end_date": "2027-01-01",
                "daily_late_fee_pct": "0.1",
            },
            headers=owner_a["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
        assert response.json()["error"]["field"] == "renter_id"

    async def test_maintenance_role_has_no_contract_permissions(self, client, seed):
        """RN-A01: `maintenance` no tiene ningun permiso `contract:*` --
        403 FORBIDDEN antes de llegar al service."""
        org, _owner = await _seed_org_with_owner(seed, name="Org Maintenance")
        maintenance = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["maintenance"],
            role_name="maintenance",
        )

        response = await client.get("/v1/contracts", headers=maintenance["headers"])

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"


class TestAdjustmentCrossTenantIsolation:
    """RN-D01 (issue #18): un ajuste de la org B nunca es accesible ni
    aplicable desde la org A."""

    async def test_get_contract_adjustments_of_another_organization_returns_404(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        _org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")
        property_b, renter_b = await _seed_property_and_renter(seed, owner_b["organization_id"])
        contract_b_id = await seed.create_contract_row(
            organization_id=owner_b["organization_id"],
            property_id=property_b,
            renter_id=renter_b,
            status="active",
        )
        await seed.create_adjustment_row(
            organization_id=owner_b["organization_id"], contract_id=contract_b_id
        )

        response = await client.get(
            f"/v1/contracts/{contract_b_id}/adjustments", headers=owner_a["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_pending_inbox_never_returns_another_organizations_adjustments(
        self, client, seed
    ):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        _org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")
        property_b, renter_b = await _seed_property_and_renter(seed, owner_b["organization_id"])
        contract_b_id = await seed.create_contract_row(
            organization_id=owner_b["organization_id"],
            property_id=property_b,
            renter_id=renter_b,
            status="active",
        )
        adjustment_b_id = await seed.create_adjustment_row(
            organization_id=owner_b["organization_id"], contract_id=contract_b_id
        )

        response = await client.get(
            "/v1/adjustments", params={"status": "pending"}, headers=owner_a["headers"]
        )

        assert response.status_code == 200
        ids = {item["id"] for item in response.json()["data"]}
        assert str(adjustment_b_id) not in ids

    async def test_apply_adjustment_of_another_organization_returns_404(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        _org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")
        property_b, renter_b = await _seed_property_and_renter(seed, owner_b["organization_id"])
        contract_b_id = await seed.create_contract_row(
            organization_id=owner_b["organization_id"],
            property_id=property_b,
            renter_id=renter_b,
            status="active",
        )
        adjustment_b_id = await seed.create_adjustment_row(
            organization_id=owner_b["organization_id"], contract_id=contract_b_id
        )

        response = await client.post(
            f"/v1/adjustments/{adjustment_b_id}/apply",
            json={"pct": "5.00"},
            headers=owner_a["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

        # El ajuste de la org B no debe haber sido modificado.
        untouched = await client.get(
            f"/v1/contracts/{contract_b_id}/adjustments", headers=owner_b["headers"]
        )
        assert untouched.json()["data"][0]["status"] == "pending"
