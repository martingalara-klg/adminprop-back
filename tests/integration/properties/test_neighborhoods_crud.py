"""tests/integration/properties/test_neighborhoods_crud.py

SDD: docs/sdd/features/spec_module_01_propiedades.md RF-05 (issue #99).
Implements: CA-01-07.
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


class TestCA0107NeighborhoodCrud:
    """CA-01-07: ABM de barrios funcionando con unicidad por org y soft
    delete protegido por dependencias."""

    async def test_ca_01_07_create_neighborhood(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.post(
            "/v1/neighborhoods",
            json={"name": "Nueva Cordoba"},
            headers=owner["headers"],
        )

        assert response.status_code == 201
        assert response.json()["data"]["name"] == "Nueva Cordoba"

    async def test_ca_01_07_created_neighborhood_appears_in_listing(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        created = await client.post(
            "/v1/neighborhoods", json={"name": "Guemes"}, headers=owner["headers"]
        )
        neighborhood_id = created.json()["data"]["id"]

        response = await client.get("/v1/neighborhoods", headers=owner["headers"])

        assert response.status_code == 200
        ids = {item["id"] for item in response.json()["data"]}
        assert neighborhood_id in ids

    async def test_ca_01_07_create_duplicate_name_case_insensitive_returns_409(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        await client.post(
            "/v1/neighborhoods", json={"name": "Alta Cordoba"}, headers=owner["headers"]
        )

        response = await client.post(
            "/v1/neighborhoods", json={"name": "ALTA CORDOBA"}, headers=owner["headers"]
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CONFLICT"

    async def test_ca_01_07_rename_neighborhood(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        created = await client.post(
            "/v1/neighborhoods", json={"name": "Barrio Viejo"}, headers=owner["headers"]
        )
        neighborhood_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/neighborhoods/{neighborhood_id}",
            json={"name": "Barrio Renombrado"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        assert response.json()["data"]["name"] == "Barrio Renombrado"

    async def test_ca_01_07_rename_to_existing_name_returns_409(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        await client.post(
            "/v1/neighborhoods", json={"name": "Cofico"}, headers=owner["headers"]
        )
        created = await client.post(
            "/v1/neighborhoods", json={"name": "General Paz"}, headers=owner["headers"]
        )
        neighborhood_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/neighborhoods/{neighborhood_id}",
            json={"name": "Cofico"},
            headers=owner["headers"],
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CONFLICT"

    async def test_ca_01_07_rename_to_own_current_name_is_allowed(self, client, seed):
        """No debe chocar consigo mismo -- `exclude_id` en la validacion
        de unicidad."""
        _org, owner = await _seed_org_with_owner(seed)
        created = await client.post(
            "/v1/neighborhoods", json={"name": "Villa Belgrano"}, headers=owner["headers"]
        )
        neighborhood_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/neighborhoods/{neighborhood_id}",
            json={"name": "Villa Belgrano"},
            headers=owner["headers"],
        )

        assert response.status_code == 200

    async def test_update_nonexistent_neighborhood_returns_404(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.patch(
            f"/v1/neighborhoods/{uuid.uuid4()}",
            json={"name": "No existe"},
            headers=owner["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_create_neighborhood_reader_permission_allows_list(self, client, seed):
        """RF-05: lectura con `property:read` -- un `admin` (que tiene
        `property:read` + `property:manage`, ver `ROLE_DEFINITIONS`)
        puede listar el catalogo."""
        org = await seed.create_organization_with_system_roles()
        admin = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["admin"],
            role_name="admin",
        )

        response = await client.get("/v1/neighborhoods", headers=admin["headers"])

        assert response.status_code == 200
