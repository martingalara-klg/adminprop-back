"""tests/integration/auth/test_ca_00_04_activation_disable_cycle.py

SDD: core/spec_module_00_superadmin.md CA-00-04 ("Al deshabilitar una
organizacion, sus usuarios reciben error en el proximo login o refresh;
al rehabilitarla, recuperan acceso con sus datos intactos.").

El enforcement de "disabled -> rechaza login/refresh" ya vive en
`modules/auth/repository.py::get_active_memberships` (issue #6, filtra
`o.status = 'active'`) y ya tiene tests end-to-end via disable/enable
directo en tests/integration/superadmin/test_disable_enable.py (issue #7,
con un owner sembrado directo en DB). Este archivo cierra el ciclo
completo del issue #8: el owner llega a existir via el flujo REAL de
activacion por invitacion (accept-invitation), no un seed directo, y se
verifica el ciclo disable -> refresh falla -> enable -> login funciona de
nuevo.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


class TestCA0004ActivationDisableEnableCycle:
    async def test_ca_00_04_disabled_org_rejects_refresh_and_enable_restores_login(
        self, client, seed, super_admin_headers
    ):
        # Email unico por corrida (no `_unique_email()` del seeder, que
        # genera el user directo en DB): sin este email fijo, correr la
        # suite dos veces contra el mismo Postgres local dejaba al owner
        # con una membresia activa remanente de una corrida anterior en
        # OTRA organizacion, y el login sin `organization_id` explicito
        # auto-selecciona esa unica membresia activa -> 200 en vez del 403
        # esperado (docs/skills/testing.md exige tests deterministas).
        owner_email = f"owner-ciclo-{uuid.uuid4().hex[:12]}@example.com"

        # 1. Organizacion pending_owner + invitacion (CA-00-01/02, issue #7).
        org_id = await seed.create_organization(status="pending_owner", name="Org Ciclo CA-00-04")
        role_id = await seed.create_role(org_id, name="owner", permissions=["user:manage"])
        raw_token = await seed.create_invitation(
            organization_id=org_id, role_id=role_id, email=owner_email
        )

        # 2. Activacion real via accept-invitation (CA-00-03, issue #8):
        # la organizacion pasa a active y el owner queda logueado.
        accept = await client.post(
            "/v1/auth/accept-invitation",
            json={
                "token": raw_token,
                "full_name": "Owner Del Ciclo",
                "password": "Password1234",
            },
        )
        assert accept.status_code == 201
        refresh_cookie = client.cookies.get("refresh_token")
        assert refresh_cookie is not None
        # El cliente compartido quedo con las cookies de sesion del owner
        # recien activado (access_token incluido). `requires_super_admin`
        # prioriza la cookie sobre el header Authorization (mismo criterio
        # que sdd_03 "el header Authorization: Bearer solo para
        # testing/server-to-server") -- limpiarlas para que
        # `super_admin_headers` sea lo que realmente autentique las
        # llamadas a /superadmin/* de aca en mas.
        client.cookies.clear()

        # 3. Super Admin deshabilita la organizacion.
        disable = await client.post(
            f"/v1/superadmin/organizations/{org_id}/disable",
            json={"reason": "CA-00-04: verificar ciclo completo"},
            headers=super_admin_headers,
        )
        assert disable.status_code == 200
        assert disable.json()["data"]["status"] == "disabled"

        # 4. El owner ya activado ahora falla tanto en refresh...
        refresh_response = await client.post(
            "/v1/auth/refresh", cookies={"refresh_token": refresh_cookie}
        )
        assert refresh_response.status_code == 403
        assert refresh_response.json()["error"]["code"] == "MEMBERSHIP_INACTIVE"

        # ...como en un login nuevo.
        login_while_disabled = await client.post(
            "/v1/auth/login",
            json={"email": owner_email, "password": "Password1234"},
        )
        assert login_while_disabled.status_code == 403
        assert login_while_disabled.json()["error"]["code"] == "MEMBERSHIP_INACTIVE"

        # 5. Super Admin rehabilita la organizacion.
        enable = await client.post(
            f"/v1/superadmin/organizations/{org_id}/enable",
            json={"reason": "CA-00-04: recuperar acceso"},
            headers=super_admin_headers,
        )
        assert enable.status_code == 200
        assert enable.json()["data"]["status"] == "active"

        # 6. El owner recupera el acceso con sus datos intactos (mismo
        # email/password/rol que la activacion original).
        login_after_enable = await client.post(
            "/v1/auth/login",
            json={"email": owner_email, "password": "Password1234"},
        )
        assert login_after_enable.status_code == 200
        body = login_after_enable.json()["data"]
        assert body["status"] == "authenticated"
        assert body["organizations"][0]["id"] == str(org_id)
        assert body["organizations"][0]["role"] == "owner"
