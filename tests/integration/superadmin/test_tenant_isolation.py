"""tests/integration/superadmin/test_tenant_isolation.py

RN-D01 (docs/skills/tenant-isolation.md) no aplica en su forma clasica al
portal /superadmin/*: el Super Admin opera intencionalmente sobre todas
las organizaciones (rol `adminprop_superadmin`, BYPASSRLS) -- no hay un
"tenant propio" del que aislarlo. La invariante equivalente para este
modulo es CA-00-06 (spec_module_00_superadmin.md RN-06): el portal
Super Admin solo expone metadata de organizacion/invitaciones, nunca
datos operativos (propiedades, contratos, cobros, liquidaciones) de
ninguna organizacion.
"""

import pytest

pytestmark = pytest.mark.asyncio

_OPERATIONAL_PATH_FRAGMENTS = (
    "propert",
    "contract",
    "payment",
    "settlement",
    "landlord",
    "renter",
    "work-order",
    "charge",
)


class TestCA0006SuperAdminScopeRestriction:
    """CA-00-06: El Super Admin no puede consultar propiedades, contratos,
    cobros ni liquidaciones de ninguna organizacion desde este portal."""

    async def test_ca_00_06_no_operational_routes_exist_under_superadmin_prefix(self, client):
        openapi = (await client.get("/openapi.json")).json()
        superadmin_paths = [p for p in openapi["paths"] if p.startswith("/v1/superadmin/")]

        assert superadmin_paths, "se esperaban rutas /v1/superadmin/* registradas"
        for path in superadmin_paths:
            lowered = path.lower()
            assert not any(fragment in lowered for fragment in _OPERATIONAL_PATH_FRAGMENTS), path

    async def test_ca_00_06_organization_detail_only_exposes_metadata_fields(
        self, client, super_admin_headers
    ):
        created = await client.post(
            "/v1/superadmin/organizations",
            json={"name": "Org Solo Metadata"},
            headers=super_admin_headers,
        )
        org_id = created.json()["data"]["id"]

        response = await client.get(
            f"/v1/superadmin/organizations/{org_id}", headers=super_admin_headers
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert set(data.keys()) == {
            "id",
            "slug",
            "name",
            "status",
            "timezone",
            "created_at",
            "owner_email",
            "settings",
            "updated_at",
        }

    async def test_ca_00_06_list_response_only_exposes_metadata_fields(
        self, client, super_admin_headers
    ):
        await client.post(
            "/v1/superadmin/organizations",
            json={"name": "Org Dashboard Metadata"},
            headers=super_admin_headers,
        )

        response = await client.get("/v1/superadmin/organizations", headers=super_admin_headers)

        assert response.status_code == 200
        items = response.json()["data"]
        assert items
        for item in items:
            assert set(item.keys()) == {
                "id",
                "slug",
                "name",
                "status",
                "timezone",
                "created_at",
                "owner_email",
            }
