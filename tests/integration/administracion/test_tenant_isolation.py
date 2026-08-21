"""tests/integration/administracion/test_tenant_isolation.py

SDD: core/sdd_03_api_contracts.md §"Convenciones Generales" ("Cross-tenant
y recursos inexistentes: siempre 404 NOT_FOUND, nunca 403 -- RN-D01").
Obligatorio segun docs/skills/module-structure.md checklist ("El modulo
tiene su carpeta tests/ con un test de aislamiento multi-tenant").

Nota de alcance: `sdd_03` §3 no define un `GET /users/:id` (solo el
listado `GET /users` + `PATCH`/`DELETE /users/:id`) -- este archivo cubre
aislamiento cross-tenant para PATCH y DELETE, que son los unicos
endpoints de `/users/:id` reales.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _seed_org_with_owner(seed, *, name: str):
    org = await seed.create_organization_with_system_roles(name=name)
    owner = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["owner"],
        role_name="owner",
    )
    return org, owner


class TestUserCrossTenantIsolation:
    async def test_patch_user_of_another_organization_returns_404(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        org_b, _owner_b = await _seed_org_with_owner(seed, name="Org B")
        member_b = await seed.add_member(
            organization_id=org_b["organization_id"],
            role_id=org_b["roles"]["maintenance"],
            role_name="maintenance",
        )

        response = await client.patch(
            f"/v1/users/{member_b['id']}",
            json={"role": "admin"},
            headers=owner_a["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_delete_user_of_another_organization_returns_404(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        org_b, _owner_b = await _seed_org_with_owner(seed, name="Org B")
        member_b = await seed.add_member(
            organization_id=org_b["organization_id"],
            role_id=org_b["roles"]["maintenance"],
            role_name="maintenance",
        )

        response = await client.delete(f"/v1/users/{member_b['id']}", headers=owner_a["headers"])

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_list_users_never_returns_members_of_another_organization(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        org_b, _owner_b = await _seed_org_with_owner(seed, name="Org B")
        member_b = await seed.add_member(
            organization_id=org_b["organization_id"],
            role_id=org_b["roles"]["maintenance"],
            role_name="maintenance",
        )

        response = await client.get("/v1/users", headers=owner_a["headers"])

        assert response.status_code == 200
        emails = {item["email"] for item in response.json()["data"]}
        assert member_b["email"] not in emails


class TestInvitationCrossTenantIsolation:
    async def test_resend_invitation_of_another_organization_returns_404(
        self, client, seed, sent_emails
    ):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        _org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")
        invite_response = await client.post(
            "/v1/users/invite",
            json={"email": "de-org-b@example.com", "role": "maintenance"},
            headers=owner_b["headers"],
        )
        invitation_id = invite_response.json()["data"]["id"]

        response = await client.post(
            f"/v1/users/invitations/{invitation_id}/resend", headers=owner_a["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_revoke_invitation_of_another_organization_returns_404(
        self, client, seed, sent_emails
    ):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        _org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")
        invite_response = await client.post(
            "/v1/users/invite",
            json={"email": "otra-de-org-b@example.com", "role": "admin"},
            headers=owner_b["headers"],
        )
        invitation_id = invite_response.json()["data"]["id"]

        response = await client.delete(
            f"/v1/users/invitations/{invitation_id}", headers=owner_a["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_list_invitations_never_returns_another_organizations_invitations(
        self, client, seed, sent_emails
    ):
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        _org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")
        await client.post(
            "/v1/users/invite",
            json={"email": "solo-en-b@example.com", "role": "maintenance"},
            headers=owner_b["headers"],
        )

        response = await client.get("/v1/users/invitations", headers=owner_a["headers"])

        assert response.status_code == 200
        emails = {item["email"] for item in response.json()["data"]}
        assert "solo-en-b@example.com" not in emails


class TestOrganizationSettingsCrossTenantIsolation:
    async def test_settings_are_never_shared_across_organizations(self, client, seed):
        """RN-D01: no hay `:id` en el path de `/organization/settings` --
        el aislamiento se prueba demostrando que el PUT de la organizacion
        A nunca afecta los settings de la organizacion B (cada uno se
        resuelve exclusivamente desde `organization_id` del JWT del
        request, via `get_current_tenant`)."""
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        _org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")

        baseline_b = await client.get("/v1/organization/settings", headers=owner_b["headers"])
        assert baseline_b.json()["data"]["grace_day"] == 10

        update_a = await client.put(
            "/v1/organization/settings",
            json={
                "grace_day": 20,
                "contract_expiry_notice_days": 90,
                "billing_name": "Solo Org A",
                "billing_cuit": None,
                "billing_contact": None,
            },
            headers=owner_a["headers"],
        )
        assert update_a.status_code == 200
        assert update_a.json()["data"]["grace_day"] == 20

        after_b = await client.get("/v1/organization/settings", headers=owner_b["headers"])
        assert after_b.json()["data"]["grace_day"] == 10
        assert after_b.json()["data"]["billing_header"]["name"] is None

    async def test_patch_role_unknown_user_id_in_own_organization_returns_404(self, client, seed):
        """Complementario: un `user_id` que no existe en NINGUNA
        organizacion tambien es 404 (no solo el caso cross-tenant)."""
        _org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")

        response = await client.patch(
            f"/v1/users/{uuid.uuid4()}",
            json={"role": "admin"},
            headers=owner_a["headers"],
        )

        assert response.status_code == 404


class TestAuditLogCrossTenantIsolation:
    """RN-D01 (issue #32, RF-05): el visor de auditoria nunca expone
    eventos de otra organizacion."""

    async def test_get_audit_log_of_another_organization_returns_404(self, client, seed):
        org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")

        change_in_b = await client.put(
            "/v1/organization/settings",
            json={
                "grace_day": 22,
                "contract_expiry_notice_days": 90,
                "billing_name": None,
                "billing_cuit": None,
                "billing_contact": None,
            },
            headers=owner_b["headers"],
        )
        assert change_in_b.status_code == 200

        listed_in_b = await client.get(
            "/v1/audit-logs", params={"action": "settings.changed"}, headers=owner_b["headers"]
        )
        audit_log_id_in_b = listed_in_b.json()["data"][0]["id"]

        response = await client.get(
            f"/v1/audit-logs/{audit_log_id_in_b}", headers=owner_a["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_list_audit_logs_never_returns_another_organizations_events(
        self, client, seed
    ):
        org_a, owner_a = await _seed_org_with_owner(seed, name="Org A")
        org_b, owner_b = await _seed_org_with_owner(seed, name="Org B")

        await client.put(
            "/v1/organization/settings",
            json={
                "grace_day": 18,
                "contract_expiry_notice_days": 90,
                "billing_name": None,
                "billing_cuit": None,
                "billing_contact": None,
            },
            headers=owner_b["headers"],
        )

        response = await client.get(
            "/v1/audit-logs", params={"action": "settings.changed"}, headers=owner_a["headers"]
        )

        assert response.status_code == 200
        assert response.json()["data"] == []
