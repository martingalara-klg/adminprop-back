"""tests/integration/people/test_entity_has_dependencies.py

SDD: docs/sdd/features/spec_module_02_personas.md RF-01/RF-03.
Implements: CA-02-06.
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


class TestCA0206DeleteWithoutDependenciesSoftDeletes:
    """CA-02-06 (mitad "sin dependencias"): sin dependencias, la baja es
    logica. La mitad "con dependencias -> 409 ENTITY_HAS_DEPENDENCIES" no
    es ejercitable end-to-end en este PR: los modulos `properties` y
    `contracts` (que originan esas dependencias) todavia no existen --
    ver `LandlordRepository.has_active_dependencies` /
    `RenterRepository.has_active_dependencies`, cuyo docstring documenta
    la extensibilidad deliberada (siempre `False` hoy, reemplazable por
    un EXISTS real cuando el modulo dependiente exista).
    """

    async def test_delete_landlord_without_properties_returns_204(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        created = await client.post(
            "/v1/landlords",
            json={"name": "Sin dependencias", "commission_pct": "10"},
            headers=owner["headers"],
        )
        landlord_id = created.json()["data"]["id"]

        response = await client.delete(f"/v1/landlords/{landlord_id}", headers=owner["headers"])

        assert response.status_code == 204

    async def test_delete_renter_without_contracts_returns_204(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        created = await client.post(
            "/v1/renters", json={"name": "Sin contrato"}, headers=owner["headers"]
        )
        renter_id = created.json()["data"]["id"]

        response = await client.delete(f"/v1/renters/{renter_id}", headers=owner["headers"])

        assert response.status_code == 204

    async def test_delete_already_deleted_landlord_returns_404_not_409(self, client, seed):
        """Complementario: una segunda baja sobre el mismo recurso es
        404 (ya no existe, RN-D01/RN-D02), nunca 409."""
        _org, owner = await _seed_org_with_owner(seed)
        created = await client.post(
            "/v1/landlords",
            json={"name": "Doble baja", "commission_pct": "10"},
            headers=owner["headers"],
        )
        landlord_id = created.json()["data"]["id"]
        await client.delete(f"/v1/landlords/{landlord_id}", headers=owner["headers"])

        second_delete = await client.delete(
            f"/v1/landlords/{landlord_id}", headers=owner["headers"]
        )

        assert second_delete.status_code == 404
        assert second_delete.json()["error"]["code"] == "NOT_FOUND"


class TestHasActiveDependenciesExtensibilityDocumented:
    """Verifica a nivel de repository (no HTTP) que el chequeo extensible
    existe con la firma correcta y hoy siempre retorna `False` --
    documenta explicitamente el alcance actual de CA-02-06 (issue #13)
    de forma independiente del flujo HTTP de arriba."""

    async def test_landlord_has_active_dependencies_is_always_false_today(self, seed):
        from adminprop.db.session import get_session_factory
        from adminprop.modules.people.repository import LandlordRepository

        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])

        session_factory = get_session_factory()
        async with session_factory() as session:
            repo = LandlordRepository(session)
            result = await repo.has_active_dependencies(landlord_id, owner["organization_id"])

        assert result is False

    async def test_renter_has_active_dependencies_is_always_false_today(self, seed):
        from adminprop.db.session import get_session_factory
        from adminprop.modules.people.repository import RenterRepository

        _org, owner = await _seed_org_with_owner(seed)
        renter_id = await seed.create_renter_row(organization_id=owner["organization_id"])

        session_factory = get_session_factory()
        async with session_factory() as session:
            repo = RenterRepository(session)
            result = await repo.has_active_dependencies(renter_id, owner["organization_id"])

        assert result is False
