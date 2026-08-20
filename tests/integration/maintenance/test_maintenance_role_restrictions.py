"""tests/integration/maintenance/test_maintenance_role_restrictions.py --
issue #26.

SDD: core/sdd_02_domain_model.md §RN-A01 ("El rol maintenance accede
UNICAMENTE al modulo de mantenimiento... nunca a contratos, cobranzas,
liquidaciones ni datos de propietarios/inquilinos. Enforzado en API, no
solo en UI.") + §RN-A04 ("Todo intento de acceso no autorizado queda
registrado en el log de auditoria"). Covers: CA-06-06.

Alcance: el catalogo de `MAINTENANCE_PERMISSIONS`
(`modules/superadmin/provisioning.py`, issue #7/#9) ya excluye TODOS los
permisos de contratos/cobranzas/propietarios/inquilinos/liquidaciones --
este modulo (#26) no cambia ese catalogo (fuera de alcance, "NO tocar
sdd_03"), solo agrega los tests que verifican el enforcement real contra
los endpoints YA existentes de esos modulos + la auditoria (RN-A04, ya
implementada por `shared/rbac.requires_permission`, issue #9). No existe
todavia un endpoint de liquidaciones (Modulo 5, issue #29) -- se testean
los 4 dominios con endpoint real: contratos, cobros, propietarios,
inquilinos.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
async def maintenance_user(seed):
    org = await seed.create_organization_with_system_roles()
    user = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["maintenance"],
        role_name="maintenance",
    )
    return user, org


class TestCA0606MaintenanceRoleRestrictions:
    """CA-06-06: "Un usuario `maintenance` recibe `403`/`404`... al
    intentar acceder a contratos, cobros, liquidaciones, propietarios o
    inquilinos; el intento queda auditado"."""

    @pytest.mark.asyncio
    async def test_ca_06_06_maintenance_cannot_list_contracts(self, client, maintenance_user):
        user, _org = maintenance_user

        response = await client.get("/v1/contracts", headers=user["headers"])

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    @pytest.mark.asyncio
    async def test_ca_06_06_maintenance_cannot_list_rent_periods_cobros(
        self, client, maintenance_user
    ):
        user, _org = maintenance_user

        response = await client.get("/v1/rent-periods", headers=user["headers"])

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    @pytest.mark.asyncio
    async def test_ca_06_06_maintenance_cannot_list_landlords_propietarios(
        self, client, maintenance_user
    ):
        user, _org = maintenance_user

        response = await client.get("/v1/landlords", headers=user["headers"])

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    @pytest.mark.asyncio
    async def test_ca_06_06_maintenance_cannot_list_renters_inquilinos(
        self, client, maintenance_user
    ):
        user, _org = maintenance_user

        response = await client.get("/v1/renters", headers=user["headers"])

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    @pytest.mark.asyncio
    async def test_ca_06_06_denied_access_attempts_are_audited(
        self, client, maintenance_user, seed
    ):
        user, org = maintenance_user

        await client.get("/v1/contracts", headers=user["headers"])
        await client.get("/v1/landlords", headers=user["headers"])

        denials = await seed.audit_rows(org["organization_id"], "access.denied")
        # RN-A04: cada intento no autorizado queda en audit_logs -- al
        # menos uno por request de arriba, con el user_id correcto.
        assert len(denials) >= 2
        assert all(row["user_id"] == user["id"] for row in denials)

    @pytest.mark.asyncio
    async def test_maintenance_role_can_still_use_its_own_module(self, client, maintenance_user):
        """Control negativo: `work-order:read` SI esta permitido -- RN-A01
        restringe a "otros modulos", no a mantenimiento mismo."""
        user, _org = maintenance_user

        response = await client.get("/v1/work-orders", headers=user["headers"])

        assert response.status_code == 200
