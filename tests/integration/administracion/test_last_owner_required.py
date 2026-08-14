"""tests/integration/administracion/test_last_owner_required.py

SDD: docs/sdd/features/spec_module_07_administracion.md RF-02, RN-02 (=
RN-A03). core/sdd_03_api_contracts.md §3 ("DELETE /users/:id y PATCH de
rol validan LAST_OWNER_REQUIRED").
Implements: CA-07-02 ("Desactivar al unico owner activo o cambiarle el
rol devuelve 422 LAST_OWNER_REQUIRED").
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


class TestCA0702LastOwnerRequired:
    async def test_ca_07_02_delete_last_owner_returns_422(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.delete(f"/v1/users/{owner['id']}", headers=owner["headers"])

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "LAST_OWNER_REQUIRED"

    async def test_ca_07_02_change_role_of_last_owner_returns_422(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.patch(
            f"/v1/users/{owner['id']}",
            json={"role": "admin"},
            headers=owner["headers"],
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "LAST_OWNER_REQUIRED"

    async def test_delete_owner_when_two_owners_exist_succeeds(self, client, seed):
        """Con 2 owners activos, desactivar a uno de ellos si esta
        permitido (todavia queda >= 1 owner activo)."""
        org, owner_a = await _seed_org_with_owner(seed)
        owner_b = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )

        response = await client.delete(f"/v1/users/{owner_a['id']}", headers=owner_b["headers"])

        assert response.status_code == 204

    async def test_sequential_concurrency_second_delete_of_last_owner_fails(self, client, seed):
        """Simula "dos deletes concurrentes al mismo owner-a-punto-de-ser-
        el-ultimo" de forma secuencial (no requiere asyncio.gather con dos
        conexiones reales): el `SELECT ... FOR UPDATE` de
        `count_active_owners_locked` es lo que garantiza la correccion
        bajo concurrencia real -- este test solo verifica el resultado
        observable, que es equivalente en un escenario secuencial:

        1. Organizacion con exactamente 2 owners activos.
        2. DELETE sobre el owner A -> 204 (queda 1 owner activo).
        3. DELETE sobre el owner B (el que quedo) -> 422
           LAST_OWNER_REQUIRED (ya es el ultimo).
        """
        org, owner_a = await _seed_org_with_owner(seed)
        owner_b = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )

        first_delete = await client.delete(f"/v1/users/{owner_a['id']}", headers=owner_b["headers"])
        assert first_delete.status_code == 204

        second_delete = await client.delete(
            f"/v1/users/{owner_b['id']}", headers=owner_b["headers"]
        )
        assert second_delete.status_code == 422
        assert second_delete.json()["error"]["code"] == "LAST_OWNER_REQUIRED"

    async def test_change_role_of_owner_when_two_owners_exist_succeeds(self, client, seed):
        org, owner_a = await _seed_org_with_owner(seed)
        owner_b = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )

        response = await client.patch(
            f"/v1/users/{owner_a['id']}",
            json={"role": "admin"},
            headers=owner_b["headers"],
        )

        assert response.status_code == 200
        assert response.json()["data"]["role_name"] == "admin"
