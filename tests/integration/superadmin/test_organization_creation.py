"""tests/integration/superadmin/test_organization_creation.py

SDD: core/spec_module_00_superadmin.md RF-02
     + infrastructure/spec_data_model.md §"Estrategia de Seed Data".
"""

import uuid

import pytest

from adminprop.modules.superadmin.provisioning import slugify

pytestmark = pytest.mark.asyncio


def _unique_name(base: str) -> str:
    """`slug` es UNIQUE global -- un nombre fijo colisionaria entre
    corridas repetidas de la suite contra el mismo Postgres persistente
    (docker-compose local, sin rollback por test). Mismo patron que
    `_unique_email()` en tests/integration/auth/conftest.py."""
    return f"{base} {uuid.uuid4().hex[:8]}"


class TestCA0001OrganizationCreation:
    """CA-00-01: El Super Admin crea una organizacion y queda `pending_owner`,
    con slug autogenerado unico y sus 3 roles de sistema + settings default
    sembrados en la misma transaccion."""

    async def test_ca_00_01_creates_org_in_pending_owner_with_default_settings(
        self, client, super_admin_headers
    ):
        name = _unique_name("Acme Propiedades")

        response = await client.post(
            "/v1/superadmin/organizations",
            json={"name": name},
            headers=super_admin_headers,
        )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["status"] == "pending_owner"
        assert data["slug"] == slugify(name)
        assert data["timezone"] == "America/Argentina/Cordoba"
        assert data["settings"] == {"grace_day": 10, "contract_expiry_notice_days": 60}
        assert data["owner_email"] is None

    async def test_ca_00_01_seeds_exactly_three_system_roles_in_same_transaction(
        self, client, super_admin_headers, db_roles
    ):
        response = await client.post(
            "/v1/superadmin/organizations",
            json={"name": _unique_name("Roles Invariant Org")},
            headers=super_admin_headers,
        )
        org_id = response.json()["data"]["id"]

        roles = await db_roles(org_id)

        assert sorted(role["name"] for role in roles) == ["admin", "maintenance", "owner"]
        assert all(role["is_system_role"] for role in roles)

    async def test_ca_00_01_owner_role_has_every_atomic_permission(
        self, client, super_admin_headers, db_roles
    ):
        response = await client.post(
            "/v1/superadmin/organizations",
            json={"name": _unique_name("Owner Permissions Org")},
            headers=super_admin_headers,
        )
        org_id = response.json()["data"]["id"]

        roles = {role["name"]: role["permissions"] for role in await db_roles(org_id)}

        assert "user:manage" in roles["owner"]
        assert "user:manage" not in roles["admin"]
        assert set(roles["maintenance"]) == {
            "work-order:read",
            "work-order:quote",
            "work-order:close",
            "attachment:manage",
        }

    async def test_slug_collision_appends_numeric_suffix(self, client, super_admin_headers):
        """RF-02: ante colision el slug se sufija -2, -3, ..."""
        name = _unique_name("Duplicado Org")
        expected_base_slug = slugify(name)

        first = await client.post(
            "/v1/superadmin/organizations",
            json={"name": name},
            headers=super_admin_headers,
        )
        second = await client.post(
            "/v1/superadmin/organizations",
            json={"name": name},
            headers=super_admin_headers,
        )

        assert first.json()["data"]["slug"] == expected_base_slug
        assert second.json()["data"]["slug"] == f"{expected_base_slug}-2"

    async def test_name_shorter_than_2_chars_returns_validation_error(
        self, client, super_admin_headers
    ):
        response = await client.post(
            "/v1/superadmin/organizations",
            json={"name": "A"},
            headers=super_admin_headers,
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_get_nonexistent_organization_returns_404(self, client, super_admin_headers):
        response = await client.get(
            "/v1/superadmin/organizations/00000000-0000-0000-0000-000000000000",
            headers=super_admin_headers,
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_unknown_field_in_body_is_rejected(self, client, super_admin_headers):
        """schemas.py usa `extra="forbid"` -- CLAUDE.md §8 "no aceptar campos
        fuera del SDD"."""
        response = await client.post(
            "/v1/superadmin/organizations",
            json={"name": "Org Valida", "status": "active"},
            headers=super_admin_headers,
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
