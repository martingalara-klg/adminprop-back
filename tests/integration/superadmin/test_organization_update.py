"""tests/integration/superadmin/test_organization_update.py

SDD: core/sdd_03_api_contracts.md §2 "PATCH /superadmin/organizations/:id"
     + core/spec_module_00_superadmin.md RN-05 (auditoria).
Implements: CA-44-01.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_session_factory

pytestmark = pytest.mark.asyncio


def _unique_name(base: str) -> str:
    """`slug` es UNIQUE global -- evita colisiones entre corridas repetidas
    de la suite contra el mismo Postgres persistente (mismo patron que
    test_organization_creation.py)."""
    return f"{base} {uuid.uuid4().hex[:8]}"


async def _create_org(client, super_admin_headers, name: str, timezone: str | None = None) -> str:
    body = {"name": name}
    if timezone is not None:
        body["timezone"] = timezone
    response = await client.post(
        "/v1/superadmin/organizations", json=body, headers=super_admin_headers
    )
    return response.json()["data"]["id"]


async def _audit_rows(organization_id: str, action: str) -> list[dict]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        result = await session.execute(
            sa.text(
                "SELECT entity_id, user_id, before_state, after_state FROM audit_logs "
                "WHERE organization_id = :org_id AND action = :action ORDER BY created_at"
            ),
            {"org_id": str(organization_id), "action": action},
        )
        return [dict(row._mapping) for row in result]


class TestCA4401UpdateOrganization:
    """CA-44-01: PATCH /superadmin/organizations/:id acepta name?/timezone?
    (al menos uno), slug inmutable, status fuera de alcance, cambio
    auditado."""

    async def test_ca_44_01_updates_name_only(self, client, super_admin_headers):
        org_id = await _create_org(client, super_admin_headers, _unique_name("Org Nombre"))
        new_name = _unique_name("Org Renombrada")

        response = await client.patch(
            f"/v1/superadmin/organizations/{org_id}",
            json={"name": new_name},
            headers=super_admin_headers,
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["name"] == new_name
        assert data["timezone"] == "America/Argentina/Cordoba"

    async def test_ca_44_01_updates_timezone_only(self, client, super_admin_headers):
        org_id = await _create_org(client, super_admin_headers, _unique_name("Org Timezone"))

        response = await client.patch(
            f"/v1/superadmin/organizations/{org_id}",
            json={"timezone": "America/New_York"},
            headers=super_admin_headers,
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["timezone"] == "America/New_York"

    async def test_ca_44_01_updates_both_fields(self, client, super_admin_headers):
        org_id = await _create_org(client, super_admin_headers, _unique_name("Org Ambos"))
        new_name = _unique_name("Org Ambos Renombrada")

        response = await client.patch(
            f"/v1/superadmin/organizations/{org_id}",
            json={"name": new_name, "timezone": "Europe/Madrid"},
            headers=super_admin_headers,
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["name"] == new_name
        assert data["timezone"] == "Europe/Madrid"

    async def test_empty_body_returns_validation_error(self, client, super_admin_headers):
        org_id = await _create_org(client, super_admin_headers, _unique_name("Org Body Vacio"))

        response = await client.patch(
            f"/v1/superadmin/organizations/{org_id}",
            json={},
            headers=super_admin_headers,
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_disallowed_field_slug_returns_validation_error(
        self, client, super_admin_headers
    ):
        """`slug` es inmutable post-creacion -- `extra="forbid"` lo rechaza."""
        org_id = await _create_org(client, super_admin_headers, _unique_name("Org Slug"))

        response = await client.patch(
            f"/v1/superadmin/organizations/{org_id}",
            json={"slug": "otro-slug"},
            headers=super_admin_headers,
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_disallowed_field_status_returns_validation_error(
        self, client, super_admin_headers
    ):
        """`status` solo cambia via disable/enable, nunca por este PATCH."""
        org_id = await _create_org(client, super_admin_headers, _unique_name("Org Status"))

        response = await client.patch(
            f"/v1/superadmin/organizations/{org_id}",
            json={"status": "disabled"},
            headers=super_admin_headers,
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_invalid_timezone_returns_validation_error(self, client, super_admin_headers):
        org_id = await _create_org(client, super_admin_headers, _unique_name("Org TZ Invalida"))

        response = await client.patch(
            f"/v1/superadmin/organizations/{org_id}",
            json={"timezone": "No/Existe"},
            headers=super_admin_headers,
        )

        assert response.status_code == 400
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert body["error"]["field"] == "timezone"

    async def test_nonexistent_organization_returns_404(self, client, super_admin_headers):
        response = await client.patch(
            "/v1/superadmin/organizations/00000000-0000-0000-0000-000000000000",
            json={"name": "No Existe"},
            headers=super_admin_headers,
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_non_super_admin_jwt_returns_superadmin_required(
        self, client, super_admin_headers, owner_headers
    ):
        org_id = await _create_org(client, super_admin_headers, _unique_name("Org No SuperAdmin"))

        response = await client.patch(
            f"/v1/superadmin/organizations/{org_id}",
            json={"name": "Intento No Autorizado"},
            headers=owner_headers,
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "SUPERADMIN_REQUIRED"

    async def test_update_writes_org_updated_audit_row_with_before_after(
        self, client, super_admin_headers
    ):
        original_name = _unique_name("Org Auditada")
        org_id = await _create_org(
            client, super_admin_headers, original_name, timezone="America/Argentina/Cordoba"
        )
        new_name = _unique_name("Org Auditada Renombrada")

        response = await client.patch(
            f"/v1/superadmin/organizations/{org_id}",
            json={"name": new_name, "timezone": "America/New_York"},
            headers=super_admin_headers,
        )
        assert response.status_code == 200

        rows = await _audit_rows(org_id, "org.updated")
        assert len(rows) == 1
        assert rows[0]["before_state"] == {
            "name": original_name,
            "timezone": "America/Argentina/Cordoba",
        }
        assert rows[0]["after_state"] == {
            "name": new_name,
            "timezone": "America/New_York",
        }

    async def test_update_with_unchanged_values_does_not_write_audit_row(
        self, client, super_admin_headers
    ):
        name = _unique_name("Org Sin Cambios")
        org_id = await _create_org(
            client, super_admin_headers, name, timezone="America/Argentina/Cordoba"
        )

        response = await client.patch(
            f"/v1/superadmin/organizations/{org_id}",
            json={"name": name, "timezone": "America/Argentina/Cordoba"},
            headers=super_admin_headers,
        )

        assert response.status_code == 200
        rows = await _audit_rows(org_id, "org.updated")
        assert rows == []
