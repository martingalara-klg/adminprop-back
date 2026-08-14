"""tests/integration/administracion/test_accept_invitation_flow.py

SDD: docs/sdd/features/spec_module_07_administracion.md RF-01 ("Mismo
flujo de activacion de cuenta que el Modulo 0"). core/sdd_03_api_contracts.md
§1 "POST /auth/accept-invitation".
Implements: CA-07-01 completo (invita -> activa -> solo ve su modulo).

El endpoint de aceptacion de invitacion (`/v1/auth/accept-invitation`) es
generico y ya vive en `modules/auth/router.py` (issue #8) -- este test no
duplica esa logica, solo verifica que el flujo de invitacion de
`administracion` (issue #9) es compatible con el.
"""

from __future__ import annotations

import re

import jwt as pyjwt
import pytest

from adminprop.modules.superadmin.provisioning import (
    ADMIN_PERMISSIONS,
    MAINTENANCE_PERMISSIONS,
    OWNER_PERMISSIONS,
)
from adminprop.shared.auth.cookies import ACCESS_TOKEN_COOKIE

pytestmark = pytest.mark.asyncio

_TOKEN_RE = re.compile(r"token=([\w-]+)")


def _extract_token(sent_emails: list[dict]) -> str:
    html = sent_emails[-1]["html"]
    match = _TOKEN_RE.search(html)
    assert match is not None
    return match.group(1)


def _decode_access_token(client) -> dict:
    raw_token = client.cookies.get(ACCESS_TOKEN_COOKIE)
    assert raw_token is not None
    return pyjwt.decode(raw_token, options={"verify_signature": False})


class TestCA0701AcceptInvitationFlow:
    async def test_ca_07_01_owner_invites_maintenance_and_activates(
        self, client, seed, sent_emails
    ):
        """CA-07-01: "El owner invita a un usuario con rol maintenance; el
        invitado activa la cuenta y solo ve el modulo de mantenimiento"."""
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )

        invite_response = await client.post(
            "/v1/users/invite",
            json={"email": "maintenance-user@example.com", "role": "maintenance"},
            headers=owner["headers"],
        )
        assert invite_response.status_code == 201
        raw_token = _extract_token(sent_emails)

        accept_response = await client.post(
            "/v1/auth/accept-invitation",
            json={
                "token": raw_token,
                "full_name": "Usuario Mantenimiento",
                "password": "Password1234",
            },
        )

        assert accept_response.status_code == 201
        data = accept_response.json()["data"]
        assert data["organization"]["role"] == "maintenance"
        assert data["organization"]["id"] == str(org["organization_id"])

        claims = _decode_access_token(client)
        assert sorted(claims["permissions"]) == sorted(MAINTENANCE_PERMISSIONS)
        # "solo ve el modulo de mantenimiento": no tiene ningun permiso
        # exclusivo de owner/admin (ej: user:manage, organization:configure).
        assert not (
            set(claims["permissions"]) & (set(OWNER_PERMISSIONS) - set(MAINTENANCE_PERMISSIONS))
        )
        assert "user:manage" not in claims["permissions"]
        assert "organization:configure" not in claims["permissions"]

    async def test_owner_invites_admin_and_activates_with_admin_permissions(
        self, client, seed, sent_emails
    ):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )

        invite_response = await client.post(
            "/v1/users/invite",
            json={"email": "admin-user@example.com", "role": "admin"},
            headers=owner["headers"],
        )
        assert invite_response.status_code == 201
        raw_token = _extract_token(sent_emails)

        accept_response = await client.post(
            "/v1/auth/accept-invitation",
            json={"token": raw_token, "full_name": "Usuario Admin", "password": "Password1234"},
        )

        assert accept_response.status_code == 201
        claims = _decode_access_token(client)
        assert sorted(claims["permissions"]) == sorted(ADMIN_PERMISSIONS)
        assert "user:manage" not in claims["permissions"]
