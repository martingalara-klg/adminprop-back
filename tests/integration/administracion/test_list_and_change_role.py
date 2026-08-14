"""tests/integration/administracion/test_list_and_change_role.py

SDD: docs/sdd/features/spec_module_07_administracion.md RF-02.
core/sdd_03_api_contracts.md §3 "GET /users", "PATCH /users/:id".
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


class TestListUsers:
    async def test_list_users_returns_members_with_role_and_status(self, client, seed):
        org, owner = await _seed_org_with_owner(seed)
        admin = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["admin"],
            role_name="admin",
        )

        response = await client.get("/v1/users", headers=owner["headers"])

        assert response.status_code == 200
        emails = {item["email"]: item for item in response.json()["data"]}
        assert owner["email"] in emails
        assert emails[owner["email"]]["role_name"] == "owner"
        assert emails[owner["email"]]["status"] == "active"
        assert emails[admin["email"]]["role_name"] == "admin"

    async def test_list_users_paginates_with_cursor(self, client, seed):
        org, owner = await _seed_org_with_owner(seed)
        for _ in range(3):
            await seed.add_member(
                organization_id=org["organization_id"],
                role_id=org["roles"]["maintenance"],
                role_name="maintenance",
            )

        first_page = await client.get("/v1/users", params={"limit": 2}, headers=owner["headers"])
        assert first_page.status_code == 200
        assert len(first_page.json()["data"]) == 2
        next_cursor = first_page.json()["meta"]["next_cursor"]
        assert next_cursor is not None

        second_page = await client.get(
            "/v1/users",
            params={"limit": 2, "cursor": next_cursor},
            headers=owner["headers"],
        )
        assert second_page.status_code == 200
        assert len(second_page.json()["data"]) == 2


class TestChangeUserRole:
    async def test_change_role_from_admin_to_maintenance(self, client, seed):
        org, owner = await _seed_org_with_owner(seed)
        admin = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["admin"],
            role_name="admin",
        )

        response = await client.patch(
            f"/v1/users/{admin['id']}",
            json={"role": "maintenance"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        assert response.json()["data"]["role_name"] == "maintenance"

    async def test_change_role_from_maintenance_to_admin(self, client, seed):
        org, owner = await _seed_org_with_owner(seed)
        member = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["maintenance"],
            role_name="maintenance",
        )

        response = await client.patch(
            f"/v1/users/{member['id']}",
            json={"role": "admin"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        assert response.json()["data"]["role_name"] == "admin"

    async def test_change_role_rejects_owner_role(self, client, seed):
        """RF-02: cambiar el rol A `owner` no esta permitido via este
        endpoint -- la transferencia de owner es exclusiva de Super
        Admin en MVP."""
        org, owner = await _seed_org_with_owner(seed)
        admin = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["admin"],
            role_name="admin",
        )

        response = await client.patch(
            f"/v1/users/{admin['id']}",
            json={"role": "owner"},
            headers=owner["headers"],
        )

        # sdd_03 §"Codigos de Error Globales": VALIDATION_ERROR es 400 en
        # este proyecto (Pydantic `Literal` rechaza antes de llegar al
        # service).
        assert response.status_code == 400

    async def test_change_role_unknown_user_returns_404(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.patch(
            f"/v1/users/{uuid.uuid4()}",
            json={"role": "admin"},
            headers=owner["headers"],
        )

        assert response.status_code == 404
