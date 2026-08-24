"""Tests de `permissions[]` / `is_super_admin` en POST /v1/auth/login (issue #84).

SDD: core/sdd_03_api_contracts.md v1.6 §1 -- "login ... incluye ...
permissions[] e is_super_admin". El front no puede leer el JWT porque
vive en cookie HttpOnly (decision #20); estos campos exponen exactamente
los mismos valores que porta el JWT emitido en el request, leidos de la
misma consulta de membresia que ya arma el JWT (sin duplicar la logica
de permisos).
"""

import pytest

pytestmark = pytest.mark.asyncio


class TestLoginPermissionsByRole:
    """CA-84-01: login devuelve permissions[] correctos por rol."""

    async def test_ca_84_01_owner_login_returns_owner_permissions_including_set_commission(
        self, client, seed
    ):
        """owner incluye `landlord:set-commission` (issue #51) -- permiso
        exclusivo de owner, no presente en admin ni maintenance."""
        owner_permissions = ["landlord:manage", "landlord:set-commission", "user:manage"]
        member = await seed.create_active_member_with_org(
            role_name="owner", permissions=owner_permissions
        )

        response = await client.post(
            "/v1/auth/login", json={"email": member["email"], "password": member["password"]}
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert sorted(data["permissions"]) == sorted(owner_permissions)
        assert "landlord:set-commission" in data["permissions"]
        assert data["is_super_admin"] is False

    async def test_ca_84_01_admin_login_returns_admin_permissions_without_set_commission(
        self, client, seed
    ):
        """admin conserva `landlord:manage` (ABM completo) pero NUNCA
        `landlord:set-commission` (sdd_03 v1.5, issue #51)."""
        admin_permissions = ["landlord:manage", "contract:manage"]
        member = await seed.create_active_member_with_org(
            role_name="admin", permissions=admin_permissions
        )

        response = await client.post(
            "/v1/auth/login", json={"email": member["email"], "password": member["password"]}
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert sorted(data["permissions"]) == sorted(admin_permissions)
        assert "landlord:set-commission" not in data["permissions"]
        assert data["is_super_admin"] is False

    async def test_ca_84_01_maintenance_login_returns_restricted_work_order_permissions(
        self, client, seed
    ):
        """maintenance SOLO ve permisos del modulo de mantenimiento
        (RN-A, sdd_03 §Resumen de Autorizacion por Recurso)."""
        maintenance_permissions = ["work-order:read", "work-order:quote", "work-order:close"]
        member = await seed.create_active_member_with_org(
            role_name="maintenance", permissions=maintenance_permissions
        )

        response = await client.post(
            "/v1/auth/login", json={"email": member["email"], "password": member["password"]}
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert sorted(data["permissions"]) == sorted(maintenance_permissions)
        assert "landlord:manage" not in data["permissions"]
        assert data["is_super_admin"] is False


class TestLoginIsSuperAdmin:
    """CA-84-02: `is_super_admin` true solo para super admin real."""

    async def test_ca_84_02_super_admin_login_returns_is_super_admin_true_and_no_permissions(
        self, client, seed
    ):
        user = await seed.create_user(is_super_admin=True)

        response = await client.post(
            "/v1/auth/login", json={"email": user["email"], "password": user["password"]}
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["is_super_admin"] is True
        # El JWT de Super Admin no lleva `org`/`role` (sdd_03 §Convenciones)
        # -- sin organizacion no hay permisos atomicos que resolver.
        assert data["permissions"] == []

    async def test_ca_84_02_regular_user_login_returns_is_super_admin_false(self, client, seed):
        member = await seed.create_active_member_with_org()

        response = await client.post(
            "/v1/auth/login", json={"email": member["email"], "password": member["password"]}
        )

        assert response.status_code == 200
        assert response.json()["data"]["is_super_admin"] is False
