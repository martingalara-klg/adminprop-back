"""tests/integration/people/test_maintenance_forbidden.py

SDD: docs/sdd/features/spec_module_02_personas.md "Actores" (RN-A01:
"maintenance -- Nada -- nunca accede a datos de propietarios ni
inquilinos") + core/sdd_03_api_contracts.md §"Resumen de Autorizacion
por Recurso".
Implements: CA-02-07.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _seed_org_with_owner_and_maintenance(seed):
    org = await seed.create_organization_with_system_roles()
    owner = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["owner"],
        role_name="owner",
    )
    maintenance = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["maintenance"],
        role_name="maintenance",
    )
    return org, owner, maintenance


class TestCA0207MaintenanceForbiddenOnLandlords:
    """CA-02-07: un usuario `maintenance` recibe 403/404 (segun sdd_03)
    en todos los endpoints de propietarios e inquilinos.

    RN-A01 se enforza via permisos atomicos (`landlord:read`/
    `landlord:manage`) que `MAINTENANCE_PERMISSIONS` no incluye -- el
    rechazo ocurre en la dependency `requires_permission`, ANTES de
    llegar al service/repository, con 403 FORBIDDEN (nunca 404, porque
    ni siquiera se resuelve el recurso)."""

    async def test_maintenance_cannot_create_landlord(self, client, seed):
        _org, _owner, maintenance = await _seed_org_with_owner_and_maintenance(seed)

        response = await client.post(
            "/v1/landlords",
            json={"name": "No deberia crearse", "commission_pct": "10"},
            headers=maintenance["headers"],
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    async def test_maintenance_cannot_list_landlords(self, client, seed):
        _org, _owner, maintenance = await _seed_org_with_owner_and_maintenance(seed)

        response = await client.get("/v1/landlords", headers=maintenance["headers"])

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    async def test_maintenance_cannot_get_landlord(self, client, seed):
        _org, owner, maintenance = await _seed_org_with_owner_and_maintenance(seed)
        created = await client.post(
            "/v1/landlords",
            json={"name": "Propietario", "commission_pct": "10"},
            headers=owner["headers"],
        )
        landlord_id = created.json()["data"]["id"]

        response = await client.get(f"/v1/landlords/{landlord_id}", headers=maintenance["headers"])

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    async def test_maintenance_cannot_patch_landlord(self, client, seed):
        _org, owner, maintenance = await _seed_org_with_owner_and_maintenance(seed)
        created = await client.post(
            "/v1/landlords",
            json={"name": "Propietario", "commission_pct": "10"},
            headers=owner["headers"],
        )
        landlord_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/landlords/{landlord_id}",
            json={"phone": "351-0000000"},
            headers=maintenance["headers"],
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    async def test_maintenance_cannot_delete_landlord(self, client, seed):
        _org, owner, maintenance = await _seed_org_with_owner_and_maintenance(seed)
        created = await client.post(
            "/v1/landlords",
            json={"name": "Propietario", "commission_pct": "10"},
            headers=owner["headers"],
        )
        landlord_id = created.json()["data"]["id"]

        response = await client.delete(
            f"/v1/landlords/{landlord_id}", headers=maintenance["headers"]
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"


class TestCA0207MaintenanceForbiddenOnRenters:
    async def test_maintenance_cannot_create_renter(self, client, seed):
        _org, _owner, maintenance = await _seed_org_with_owner_and_maintenance(seed)

        response = await client.post(
            "/v1/renters", json={"name": "No deberia crearse"}, headers=maintenance["headers"]
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    async def test_maintenance_cannot_list_renters(self, client, seed):
        _org, _owner, maintenance = await _seed_org_with_owner_and_maintenance(seed)

        response = await client.get("/v1/renters", headers=maintenance["headers"])

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    async def test_maintenance_cannot_get_renter(self, client, seed):
        _org, owner, maintenance = await _seed_org_with_owner_and_maintenance(seed)
        created = await client.post(
            "/v1/renters", json={"name": "Inquilino"}, headers=owner["headers"]
        )
        renter_id = created.json()["data"]["id"]

        response = await client.get(f"/v1/renters/{renter_id}", headers=maintenance["headers"])

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    async def test_maintenance_cannot_patch_renter(self, client, seed):
        _org, owner, maintenance = await _seed_org_with_owner_and_maintenance(seed)
        created = await client.post(
            "/v1/renters", json={"name": "Inquilino"}, headers=owner["headers"]
        )
        renter_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/renters/{renter_id}",
            json={"phone": "351-0000000"},
            headers=maintenance["headers"],
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    async def test_maintenance_cannot_delete_renter(self, client, seed):
        _org, owner, maintenance = await _seed_org_with_owner_and_maintenance(seed)
        created = await client.post(
            "/v1/renters", json={"name": "Inquilino"}, headers=owner["headers"]
        )
        renter_id = created.json()["data"]["id"]

        response = await client.delete(f"/v1/renters/{renter_id}", headers=maintenance["headers"])

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    async def test_maintenance_cannot_enumerate_unknown_landlord_either(self, client, seed):
        """Complementario: incluso un id inexistente/random es 403 antes
        de llegar al 404 -- el permiso se chequea primero."""
        _org, _owner, maintenance = await _seed_org_with_owner_and_maintenance(seed)

        response = await client.get(f"/v1/landlords/{uuid.uuid4()}", headers=maintenance["headers"])

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"
