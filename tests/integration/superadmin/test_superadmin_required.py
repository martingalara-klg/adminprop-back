"""tests/integration/superadmin/test_superadmin_required.py

SDD: core/spec_module_00_superadmin.md CA-00-05 + RN-01.
"""

import logging
import uuid

import pytest

from adminprop.shared.auth.jwt import create_access_token

pytestmark = pytest.mark.asyncio


class TestCA0005SuperAdminRequired:
    """CA-00-05: Un usuario owner/admin/maintenance que intenta acceder a
    /superadmin/* recibe 403 SUPERADMIN_REQUIRED y el intento queda auditado."""

    async def test_ca_00_05_owner_role_gets_superadmin_required_on_list(
        self, client, owner_headers
    ):
        response = await client.get("/v1/superadmin/organizations", headers=owner_headers)

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "SUPERADMIN_REQUIRED"

    async def test_ca_00_05_owner_role_gets_superadmin_required_on_create(
        self, client, owner_headers
    ):
        response = await client.post(
            "/v1/superadmin/organizations",
            json={"name": "Intento No Autorizado"},
            headers=owner_headers,
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "SUPERADMIN_REQUIRED"

    @pytest.mark.parametrize("role_name", ["owner", "admin", "maintenance"])
    async def test_ca_00_05_denies_every_org_role_regardless_of_permissions(
        self, client, rsa_keypair, role_name
    ):
        """El chequeo es sobre `is_super_admin`, nunca sobre `role`/`permissions`
        -- ningun rol de organizacion puede colarse via permisos amplios."""
        token = create_access_token(
            user_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            role=role_name,
            permissions=["contract:manage", "user:manage", "work-order:read"],
            is_super_admin=False,
            jti=str(uuid.uuid4()),
        )

        response = await client.get(
            "/v1/superadmin/organizations", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "SUPERADMIN_REQUIRED"

    async def test_ca_00_05_missing_token_returns_unauthorized_not_superadmin_required(
        self, client
    ):
        """Sin JWT, el fallo es de autenticacion (401), no de autorizacion (403)."""
        response = await client.get("/v1/superadmin/organizations")

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"

    async def test_ca_00_05_denied_attempt_is_logged_for_audit(
        self, client, owner_headers, caplog
    ):
        """TODO(#10): sin tabla `audit_logs` todavia, el intento denegado
        queda constancia en el logger estructurado (`shared/auth/dependencies.py`)."""
        with caplog.at_level(logging.WARNING, logger="adminprop.shared.auth.dependencies"):
            response = await client.get("/v1/superadmin/organizations", headers=owner_headers)

        assert response.status_code == 403
        assert any("superadmin access denied" in record.message for record in caplog.records)
