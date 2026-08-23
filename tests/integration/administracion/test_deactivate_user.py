"""tests/integration/administracion/test_deactivate_user.py

SDD: docs/sdd/features/spec_module_07_administracion.md RF-02 ("DELETE
/users/:id, soft -- la membresia pasa a inactive y no puede loguearse").
CLAUDE.md §4 / RN-A: "un usuario desactivado no puede loguearse" -- se
extiende a sus sesiones ya emitidas (refresh tokens revocados).
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from adminprop.main import create_app

pytestmark = pytest.mark.asyncio


async def _seed_org_with_owner(seed):
    org = await seed.create_organization_with_system_roles()
    owner = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["owner"],
        role_name="owner",
    )
    return org, owner


class TestDeactivateUser:
    async def test_deactivate_admin_sets_status_inactive(self, client, seed):
        org, owner = await _seed_org_with_owner(seed)
        admin = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["admin"],
            role_name="admin",
        )

        response = await client.delete(f"/v1/users/{admin['id']}", headers=owner["headers"])
        assert response.status_code == 204

        list_response = await client.get("/v1/users", headers=owner["headers"])
        item = next(row for row in list_response.json()["data"] if row["id"] == str(admin["id"]))
        assert item["status"] == "inactive"

    async def test_deactivate_unknown_user_returns_404(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.delete(f"/v1/users/{uuid.uuid4()}", headers=owner["headers"])

        assert response.status_code == 404

    async def test_deactivate_user_revokes_existing_refresh_sessions(self, client, seed):
        """Verifica el detalle de seguridad (CLAUDE.md §4 / RN-A): un
        refresh token emitido ANTES de la desactivacion deja de servir
        para renovar sesion -- end-to-end via `/v1/auth/refresh`."""
        org, owner = await _seed_org_with_owner(seed)
        member = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["maintenance"],
            role_name="maintenance",
        )

        async with AsyncClient(
            transport=ASGITransport(app=create_app()), base_url="https://testserver"
        ) as member_client:
            login_response = await member_client.post(
                "/v1/auth/login",
                json={"email": member["email"], "password": member["password"]},
            )
            assert login_response.status_code == 200
            assert member_client.cookies.get("refresh_token") is not None

            delete_response = await client.delete(
                f"/v1/users/{member['id']}", headers=owner["headers"]
            )
            assert delete_response.status_code == 204

            refresh_response = await member_client.post("/v1/auth/refresh")

            assert refresh_response.status_code == 401
