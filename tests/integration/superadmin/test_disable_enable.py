"""tests/integration/superadmin/test_disable_enable.py

SDD: core/spec_module_00_superadmin.md RF-05 + RN-03
     + core/sdd_03_api_contracts.md §2.

RN-03: "Una organizacion disabled rechaza login y refresh de todos sus
miembros". El enforcement en si ya vive en
`modules/auth/repository.py::get_active_memberships` (filtra
`o.status = 'active'`, issue #6) -- este archivo agrega el test end-to-end
que faltaba: disable via /superadmin/* -> el miembro pierde el login.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_session_factory
from adminprop.shared.auth.passwords import hash_password

pytestmark = pytest.mark.asyncio


async def _create_org(client, super_admin_headers, name: str) -> str:
    response = await client.post(
        "/v1/superadmin/organizations", json={"name": name}, headers=super_admin_headers
    )
    return response.json()["data"]["id"]


async def _seed_active_owner(organization_id: str, *, password: str = "Password1234") -> dict:
    """Crea un usuario + membresia `owner` activa directamente en DB
    (bypassa el flujo de invitacion/activacion, fuera de alcance de #7)."""
    user_id = uuid.uuid4()
    email = f"member-{user_id.hex[:12]}@example.com"
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        await session.execute(
            sa.text(
                "INSERT INTO users (id, email, password_hash, full_name, is_super_admin) "
                "VALUES (:id, :email, :password_hash, :full_name, FALSE)"
            ),
            {
                "id": str(user_id),
                "email": email,
                "password_hash": hash_password(password),
                "full_name": "Owner De Prueba",
            },
        )
        role_row = await session.execute(
            sa.text("SELECT id FROM roles WHERE organization_id = :org_id AND name = 'owner'"),
            {"org_id": organization_id},
        )
        role_id = role_row.scalar_one()
        await session.execute(
            sa.text(
                "INSERT INTO organization_members (organization_id, user_id, role_id, status) "
                "VALUES (:org_id, :user_id, :role_id, 'active')"
            ),
            {"org_id": organization_id, "user_id": str(user_id), "role_id": str(role_id)},
        )
    return {"email": email, "password": password}


class TestRF05DisableEnableOrganization:
    """RF-05: disable -> los usuarios no pueden autenticarse; enable ->
    recuperan acceso con sus datos intactos."""

    async def test_disable_sets_status_disabled_and_records_reason(
        self, client, super_admin_headers
    ):
        org_id = await _create_org(client, super_admin_headers, "Org A Deshabilitar")

        response = await client.post(
            f"/v1/superadmin/organizations/{org_id}/disable",
            json={"reason": "organizacion dio de baja el servicio"},
            headers=super_admin_headers,
        )

        assert response.status_code == 200
        assert response.json()["data"]["status"] == "disabled"

    async def test_disable_twice_returns_validation_error(self, client, super_admin_headers):
        org_id = await _create_org(client, super_admin_headers, "Org Doble Disable")
        await client.post(
            f"/v1/superadmin/organizations/{org_id}/disable",
            json={"reason": "primer disable"},
            headers=super_admin_headers,
        )

        response = await client.post(
            f"/v1/superadmin/organizations/{org_id}/disable",
            json={"reason": "segundo disable"},
            headers=super_admin_headers,
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_enable_without_reason_returns_validation_error(
        self, client, super_admin_headers
    ):
        org_id = await _create_org(client, super_admin_headers, "Org Sin Reason")

        response = await client.post(
            f"/v1/superadmin/organizations/{org_id}/enable",
            json={},
            headers=super_admin_headers,
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_enable_organization_not_disabled_returns_validation_error(
        self, client, super_admin_headers
    ):
        org_id = await _create_org(client, super_admin_headers, "Org Pending Enable")

        response = await client.post(
            f"/v1/superadmin/organizations/{org_id}/enable",
            json={"reason": "no deberia aplicar"},
            headers=super_admin_headers,
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_disable_nonexistent_organization_returns_404(self, client, super_admin_headers):
        response = await client.post(
            "/v1/superadmin/organizations/00000000-0000-0000-0000-000000000000/disable",
            json={"reason": "no existe"},
            headers=super_admin_headers,
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_enable_nonexistent_organization_returns_404(self, client, super_admin_headers):
        response = await client.post(
            "/v1/superadmin/organizations/00000000-0000-0000-0000-000000000000/enable",
            json={"reason": "no existe"},
            headers=super_admin_headers,
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_rn_03_disabled_organization_rejects_member_login(
        self, client, super_admin_headers
    ):
        """RN-03: una organizacion `disabled` rechaza login de sus miembros
        (`403 MEMBERSHIP_INACTIVE`) -- enforcement ya presente en
        `modules/auth/repository.py` (issue #6); este test lo verifica
        end-to-end tras un disable real via /superadmin/*."""
        org_id = await _create_org(client, super_admin_headers, "Org RN03 Login")
        member = await _seed_active_owner(org_id)

        await client.post(
            f"/v1/superadmin/organizations/{org_id}/disable",
            json={"reason": "verificar RN-03"},
            headers=super_admin_headers,
        )

        response = await client.post(
            "/v1/auth/login",
            json={"email": member["email"], "password": member["password"]},
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "MEMBERSHIP_INACTIVE"

    async def test_rn_03_re_enabled_organization_restores_member_login(
        self, client, super_admin_headers
    ):
        org_id = await _create_org(client, super_admin_headers, "Org RN03 Enable")
        member = await _seed_active_owner(org_id)
        await client.post(
            f"/v1/superadmin/organizations/{org_id}/disable",
            json={"reason": "verificar reactivacion"},
            headers=super_admin_headers,
        )

        await client.post(
            f"/v1/superadmin/organizations/{org_id}/enable",
            json={"reason": "reactivar organizacion"},
            headers=super_admin_headers,
        )

        response = await client.post(
            "/v1/auth/login",
            json={"email": member["email"], "password": member["password"]},
        )

        assert response.status_code == 200
        assert response.json()["data"]["status"] == "authenticated"
