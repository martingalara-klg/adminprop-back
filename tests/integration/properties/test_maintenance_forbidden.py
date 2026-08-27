"""tests/integration/properties/test_maintenance_forbidden.py

SDD: docs/sdd/features/spec_module_01_propiedades.md "Actores"
("maintenance -- Nada en este modulo") + core/sdd_03_api_contracts.md
§"Resumen de Autorizacion por Recurso".
Implements: CA-01-06.
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


class TestCA0106MaintenanceForbiddenOnProperties:
    """CA-01-06: un usuario `maintenance` no puede listar propiedades ni
    ver fichas (403/404 segun sdd_03).

    `maintenance` no tiene ningun permiso `property:*`
    (`MAINTENANCE_PERMISSIONS`) -- el rechazo ocurre en la dependency
    `requires_permission`, ANTES de llegar al service/repository, con 403
    FORBIDDEN (nunca 404, porque ni siquiera se resuelve el recurso).
    """

    async def test_ca_01_06_maintenance_cannot_create_property(self, client, seed):
        _org, owner, maintenance = await _seed_org_with_owner_and_maintenance(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )

        response = await client.post(
            "/v1/properties",
            json={
                "address": "No deberia crearse",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=maintenance["headers"],
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    async def test_ca_01_06_maintenance_cannot_list_properties(self, client, seed):
        _org, _owner, maintenance = await _seed_org_with_owner_and_maintenance(seed)

        response = await client.get("/v1/properties", headers=maintenance["headers"])

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    async def test_ca_01_06_maintenance_cannot_view_property_ficha(self, client, seed):
        _org, owner, maintenance = await _seed_org_with_owner_and_maintenance(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "Ficha protegida",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]

        response = await client.get(f"/v1/properties/{property_id}", headers=maintenance["headers"])

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    async def test_ca_01_06_maintenance_cannot_patch_property(self, client, seed):
        _org, owner, maintenance = await _seed_org_with_owner_and_maintenance(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "No editable por maintenance",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/properties/{property_id}",
            json={"notes": "hackeado"},
            headers=maintenance["headers"],
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    async def test_ca_01_06_maintenance_cannot_delete_property(self, client, seed):
        _org, owner, maintenance = await _seed_org_with_owner_and_maintenance(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "No borrable por maintenance",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]

        response = await client.delete(
            f"/v1/properties/{property_id}", headers=maintenance["headers"]
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    async def test_ca_01_06_maintenance_cannot_enumerate_unknown_property_either(
        self, client, seed
    ):
        """Complementario: incluso un id inexistente/random es 403 antes
        de llegar al 404 -- el permiso se chequea primero."""
        _org, _owner, maintenance = await _seed_org_with_owner_and_maintenance(seed)

        response = await client.get(
            f"/v1/properties/{uuid.uuid4()}", headers=maintenance["headers"]
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"


class TestCA0106MaintenanceForbiddenOnServiceAccounts:
    async def test_ca_01_06_maintenance_cannot_list_service_accounts(self, client, seed):
        _org, owner, maintenance = await _seed_org_with_owner_and_maintenance(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "Con cuentas protegidas",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]

        response = await client.get(
            f"/v1/properties/{property_id}/service-accounts", headers=maintenance["headers"]
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    async def test_ca_01_06_maintenance_cannot_create_service_account(self, client, seed):
        _org, owner, maintenance = await _seed_org_with_owner_and_maintenance(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "Sin cuentas nuevas",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]

        response = await client.post(
            f"/v1/properties/{property_id}/service-accounts",
            json={"service_type": "gas", "account_number": "GAS-1"},
            headers=maintenance["headers"],
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"


class TestCA0106MaintenanceForbiddenOnNeighborhoods:
    """Issue #99: `maintenance` tampoco tiene acceso al catalogo de
    barrios -- mismo permiso `property:*` que el resto del modulo."""

    async def test_ca_01_06_maintenance_cannot_list_neighborhoods(self, client, seed):
        _org, _owner, maintenance = await _seed_org_with_owner_and_maintenance(seed)

        response = await client.get("/v1/neighborhoods", headers=maintenance["headers"])

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    async def test_ca_01_06_maintenance_cannot_create_neighborhood(self, client, seed):
        _org, _owner, maintenance = await _seed_org_with_owner_and_maintenance(seed)

        response = await client.post(
            "/v1/neighborhoods",
            json={"name": "No deberia crearse"},
            headers=maintenance["headers"],
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    async def test_ca_01_06_maintenance_cannot_delete_neighborhood(self, client, seed):
        _org, owner, maintenance = await _seed_org_with_owner_and_maintenance(seed)
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )

        response = await client.delete(
            f"/v1/neighborhoods/{neighborhood_id}", headers=maintenance["headers"]
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"
