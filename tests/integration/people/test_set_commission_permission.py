"""tests/integration/people/test_set_commission_permission.py

SDD: docs/sdd/core/sdd_03_api_contracts.md v1.5 §"Catalogo de Permisos"
     (decision #116) + docs/sdd/features/spec_module_02_personas.md
     RF-01, RN-D04/RN-L05.
Implements: CA-R50-01, CA-R50-02, CA-R50-03 (issue #51).

Issue #51: el PR #50 (issue #13) restringia el cambio de `commission_pct`
comparando `payload.role != "owner"` en el service -- una divergencia de
CLAUDE.md §6 ("chequeo siempre por permiso atomico, nunca por nombre de
rol"). Este archivo prueba especificamente que el chequeo ahora es por el
permiso atomico `landlord:set-commission` -- no por el nombre del rol --
sembrando un rol CUSTOM (que no se llama "owner") con/sin ese permiso.
`tests/integration/people/test_commission_pct.py` (CA-02-02/CA-02-03)
sigue cubriendo el comportamiento end-to-end con los roles de sistema
reales (owner/admin) y sigue en verde sin cambios.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio


async def _seed_custom_role_member(seed, *, permissions: list[str]):
    """Siembra un rol CUSTOM (nombre `custom_manager`, NO `owner` ni
    `admin`) con el set de permisos indicado -- prueba que la
    autorizacion depende del permiso atomico en `permissions[]`, no del
    nombre del rol (CA-R50-03)."""
    org_id = await seed.create_organization(status="active")
    role_id = await seed.create_role(org_id, name="custom_manager", permissions=permissions)
    member = await seed.add_member(
        organization_id=org_id,
        role_id=role_id,
        role_name="custom_manager",
        permissions=permissions,
    )
    return org_id, member


class TestCAR5003PermissionNotRoleName:
    """CA-R50-03: el chequeo de `commission_pct` es por el permiso atomico
    `landlord:set-commission`, no por `role.name == "owner"`."""

    async def test_ca_r50_03_custom_role_with_set_commission_permission_succeeds(
        self, client, seed
    ):
        """Un rol que NO se llama "owner" pero SI tiene
        `landlord:set-commission` (ademas de `landlord:manage`) puede
        cambiar `commission_pct` -- prueba que ya no se compara el nombre
        del rol."""
        _org_id, member = await _seed_custom_role_member(
            seed, permissions=["landlord:manage", "landlord:set-commission"]
        )
        created = await client.post(
            "/v1/landlords",
            json={"name": "Propietario", "commission_pct": "10.00"},
            headers=member["headers"],
        )
        landlord_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/landlords/{landlord_id}",
            json={"commission_pct": "18.00"},
            headers=member["headers"],
        )

        assert response.status_code == 200
        assert Decimal(response.json()["data"]["commission_pct"]) == Decimal("18.00")

    async def test_ca_r50_03_custom_role_without_set_commission_permission_returns_403(
        self, client, seed
    ):
        """Un rol con `landlord:manage` (puede hacer ABM) pero SIN
        `landlord:set-commission` recibe 403 FORBIDDEN al intentar
        cambiar `commission_pct` -- mismo resultado que CA-02-02, ahora
        explicado por ausencia del permiso, no del nombre del rol."""
        _org_id, member = await _seed_custom_role_member(
            seed, permissions=["landlord:manage", "landlord:read"]
        )
        created = await client.post(
            "/v1/landlords",
            json={"name": "Propietario", "commission_pct": "10.00"},
            headers=member["headers"],
        )
        landlord_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/landlords/{landlord_id}",
            json={"commission_pct": "18.00"},
            headers=member["headers"],
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

        get_response = await client.get(f"/v1/landlords/{landlord_id}", headers=member["headers"])
        assert Decimal(get_response.json()["data"]["commission_pct"]) == Decimal("10.00")

    async def test_ca_r50_03_custom_role_can_edit_contact_data_without_commission_permission(
        self, client, seed
    ):
        """Complementario: sin `landlord:set-commission` pero con
        `landlord:manage`, el resto de los campos ("datos de contacto")
        se siguen editando sin restriccion -- el permiso nuevo es
        estrictamente ADITIVO sobre `landlord:manage`, no lo reemplaza."""
        _org_id, member = await _seed_custom_role_member(seed, permissions=["landlord:manage"])
        created = await client.post(
            "/v1/landlords",
            json={"name": "Propietario", "commission_pct": "10.00"},
            headers=member["headers"],
        )
        landlord_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/landlords/{landlord_id}",
            json={"phone": "351-4444444"},
            headers=member["headers"],
        )

        assert response.status_code == 200
        assert response.json()["data"]["phone"] == "351-4444444"


class TestCAR5001And02OwnerRoleSeededWithPermission:
    """CA-R50-01/CA-R50-02: el catalogo de permisos (sdd_03 v1.5) incluye
    `landlord:set-commission`, y el seed de roles de sistema lo asigna
    SOLO a `owner` (mismo `ROLE_DEFINITIONS` que consume
    `OrganizationProvisioningService` para organizaciones reales)."""

    async def test_ca_r50_01_and_02_owner_seeded_with_permission_admin_is_not(self, seed):
        from adminprop.modules.superadmin.provisioning import ROLE_DEFINITIONS

        permissions_by_role = dict(ROLE_DEFINITIONS)
        assert "landlord:set-commission" in permissions_by_role["owner"]
        assert "landlord:set-commission" not in permissions_by_role["admin"]
        assert "landlord:set-commission" not in permissions_by_role["maintenance"]

    async def test_ca_r50_01_and_02_seeded_owner_member_can_set_commission_e2e(self, client, seed):
        """Extremo a extremo con el seed REAL de una organizacion nueva
        (`create_organization_with_system_roles`, el mismo camino que
        `OrganizationProvisioningService`): el owner recien sembrado ya
        puede cambiar `commission_pct` sin ningun paso manual adicional."""
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )
        created = await client.post(
            "/v1/landlords",
            json={"name": "Propietario", "commission_pct": "5.00"},
            headers=owner["headers"],
        )
        landlord_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/landlords/{landlord_id}",
            json={"commission_pct": "7.50"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        assert Decimal(response.json()["data"]["commission_pct"]) == Decimal("7.50")
