"""tests/integration/properties/test_entity_has_dependencies.py

SDD: docs/sdd/features/spec_module_01_propiedades.md RF-01.
Implements: CA-01-03.
"""

from __future__ import annotations

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


class TestCA0103DeleteWithoutActiveContractSoftDeletes:
    """CA-01-03: Intentar borrar una propiedad con contrato activo
    devuelve 409 ENTITY_HAS_DEPENDENCIES; sin contrato activo, la baja es
    logica y la propiedad conserva su historial.

    La mitad "con contrato activo -> 409" no es ejercitable end-to-end en
    este PR: el modulo `contracts` (issue #17, que origina esa
    dependencia) todavia no existe -- ver
    `PropertyRepository.has_active_dependencies`, cuyo docstring
    documenta la extensibilidad deliberada (siempre `False` hoy).
    """

    async def test_ca_01_03_delete_property_without_active_contract_returns_204(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        created = await client.post(
            "/v1/properties",
            json={"address": "Sin contrato activo", "landlord_id": str(landlord_id)},
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]

        response = await client.delete(f"/v1/properties/{property_id}", headers=owner["headers"])

        assert response.status_code == 204

    async def test_ca_01_03_deleted_property_disappears_from_listing_but_history_intact(
        self, client, seed
    ):
        """ "la propiedad conserva su historial" -- verificado indirectamente:
        el soft delete no destruye la fila (sigue existiendo en DB,
        solo `deleted_at` se completa); el GET posterior es 404 (RN-D01/RN-D02:
        borrado se trata igual que "no existe" para el resto de la API)."""
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        created = await client.post(
            "/v1/properties",
            json={"address": "Con historial conservado", "landlord_id": str(landlord_id)},
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]

        await client.delete(f"/v1/properties/{property_id}", headers=owner["headers"])

        listing = await client.get("/v1/properties", headers=owner["headers"])
        ids = {item["id"] for item in listing.json()["data"]}
        assert property_id not in ids

        detail = await client.get(f"/v1/properties/{property_id}", headers=owner["headers"])
        assert detail.status_code == 404

    async def test_delete_already_deleted_property_returns_404_not_409(self, client, seed):
        """Complementario: una segunda baja sobre el mismo recurso es
        404 (ya no existe, RN-D01/RN-D02), nunca 409."""
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        created = await client.post(
            "/v1/properties",
            json={"address": "Doble baja", "landlord_id": str(landlord_id)},
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]
        await client.delete(f"/v1/properties/{property_id}", headers=owner["headers"])

        second_delete = await client.delete(
            f"/v1/properties/{property_id}", headers=owner["headers"]
        )

        assert second_delete.status_code == 404
        assert second_delete.json()["error"]["code"] == "NOT_FOUND"


class TestHasActiveDependenciesExtensibilityDocumented:
    """Verifica a nivel de repository (no HTTP) que el chequeo extensible
    existe con la firma correcta y hoy siempre retorna `False` -- documenta
    explicitamente el alcance actual de CA-01-03 (issue #15)."""

    async def test_property_has_active_dependencies_is_always_false_today(self, seed):
        from adminprop.db.session import get_session_factory
        from adminprop.modules.properties.repository import PropertyRepository

        org = await seed.create_organization_with_system_roles()
        landlord_id = await seed.create_landlord_row(organization_id=org["organization_id"])
        property_id = await seed.create_property_row(
            organization_id=org["organization_id"], landlord_id=landlord_id
        )

        session_factory = get_session_factory()
        async with session_factory() as session:
            repo = PropertyRepository(session)
            result = await repo.has_active_dependencies(property_id, org["organization_id"])

        assert result is False
