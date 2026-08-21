"""tests/integration/charges/test_tenant_isolation.py -- issue #28.

Invariante: RN-D01 -- los datos de un tenant nunca son accesibles desde
otro tenant. Cubre los 5 endpoints de `charges`
(`GET/POST /properties/:id/recurring-charges`,
`PATCH /recurring-charges/:id`, `POST /recurring-charges/:id/entries`,
`PATCH /charge-entries/:id`). `GET /charge-entries?period=` no necesita
un test cross-tenant especifico: el filtro de `organization_id` en el
JOIN garantiza que solo aparecen los conceptos del propio tenant (ver
`repository.py.list_verification`), cubierto indirectamente por
`test_verification.py`.
"""

from __future__ import annotations

import pytest


async def _seed_org_with_property(seed):
    org = await seed.create_organization_with_system_roles()
    landlord_id = await seed.create_landlord_row(organization_id=org["organization_id"])
    property_id = await seed.create_property_row(
        organization_id=org["organization_id"], landlord_id=landlord_id
    )
    return org, property_id


class TestTenantIsolation:
    """RN-D01 enforcement: tenant A no accede a recursos del tenant B."""

    @pytest.mark.asyncio
    async def test_list_recurring_charges_of_other_tenant_property_returns_404(self, client, seed):
        org_a = await seed.create_organization_with_system_roles()
        owner_a = await seed.add_member(
            organization_id=org_a["organization_id"],
            role_id=org_a["roles"]["owner"],
            role_name="owner",
        )
        _org_b, property_b_id = await _seed_org_with_property(seed)

        response = await client.get(
            f"/v1/properties/{property_b_id}/recurring-charges", headers=owner_a["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_create_recurring_charge_for_other_tenant_property_returns_404(
        self, client, seed
    ):
        org_a = await seed.create_organization_with_system_roles()
        owner_a = await seed.add_member(
            organization_id=org_a["organization_id"],
            role_id=org_a["roles"]["owner"],
            role_name="owner",
        )
        _org_b, property_b_id = await _seed_org_with_property(seed)

        response = await client.post(
            f"/v1/properties/{property_b_id}/recurring-charges",
            json={"charge_type": "otro", "label": "Intento cross-tenant"},
            headers=owner_a["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_patch_recurring_charge_of_other_tenant_returns_404(self, client, seed):
        org_a = await seed.create_organization_with_system_roles()
        owner_a = await seed.add_member(
            organization_id=org_a["organization_id"],
            role_id=org_a["roles"]["owner"],
            role_name="owner",
        )
        org_b, property_b_id = await _seed_org_with_property(seed)
        recurring_charge_b_id = await seed.create_recurring_charge_row(
            organization_id=org_b["organization_id"], property_id=property_b_id
        )

        response = await client.patch(
            f"/v1/recurring-charges/{recurring_charge_b_id}",
            json={"label": "hackeado"},
            headers=owner_a["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_create_charge_entry_for_other_tenant_recurring_charge_returns_404(
        self, client, seed
    ):
        org_a = await seed.create_organization_with_system_roles()
        owner_a = await seed.add_member(
            organization_id=org_a["organization_id"],
            role_id=org_a["roles"]["owner"],
            role_name="owner",
        )
        org_b, property_b_id = await _seed_org_with_property(seed)
        recurring_charge_b_id = await seed.create_recurring_charge_row(
            organization_id=org_b["organization_id"], property_id=property_b_id
        )

        response = await client.post(
            f"/v1/recurring-charges/{recurring_charge_b_id}/entries",
            json={"period": "2026-06", "amount": "100.00"},
            headers=owner_a["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_patch_charge_entry_of_other_tenant_returns_404(self, client, seed):
        org_a = await seed.create_organization_with_system_roles()
        owner_a = await seed.add_member(
            organization_id=org_a["organization_id"],
            role_id=org_a["roles"]["owner"],
            role_name="owner",
        )
        org_b, property_b_id = await _seed_org_with_property(seed)
        owner_b = await seed.add_member(
            organization_id=org_b["organization_id"],
            role_id=org_b["roles"]["owner"],
            role_name="owner",
        )
        recurring_charge_b_id = await seed.create_recurring_charge_row(
            organization_id=org_b["organization_id"], property_id=property_b_id
        )
        charge_entry_b_id = await seed.create_charge_entry_row(
            organization_id=org_b["organization_id"],
            recurring_charge_id=recurring_charge_b_id,
            created_by=owner_b["id"],
        )

        response = await client.patch(
            f"/v1/charge-entries/{charge_entry_b_id}",
            json={"amount": "999.00"},
            headers=owner_a["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

        # El cargo de la organizacion B NO quedo modificado.
        row = await seed.get_charge_entry(charge_entry_b_id)
        assert row["amount"] != "999.00"
