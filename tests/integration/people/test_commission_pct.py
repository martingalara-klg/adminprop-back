"""tests/integration/people/test_commission_pct.py

SDD: docs/sdd/features/spec_module_02_personas.md RF-01 + RN-D04/RN-L05.
Implements: CA-02-02, CA-02-03.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio


async def _seed_org_with_owner_and_admin(seed):
    org = await seed.create_organization_with_system_roles()
    owner = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["owner"],
        role_name="owner",
    )
    admin = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["admin"],
        role_name="admin",
    )
    return org, owner, admin


class TestCA0202AdminCannotChangeCommissionPct:
    """CA-02-02: un `admin` puede editar los datos de contacto de un
    propietario pero recibe 403 FORBIDDEN al intentar cambiar su % de
    comision; el `owner` puede cambiarlo y el cambio queda auditado con
    valor anterior y nuevo."""

    async def test_ca_02_02_admin_edits_contact_data_without_touching_commission(
        self, client, seed
    ):
        _org, owner, admin = await _seed_org_with_owner_and_admin(seed)
        created = await client.post(
            "/v1/landlords",
            json={"name": "Propietario", "commission_pct": "10.00"},
            headers=owner["headers"],
        )
        landlord_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/landlords/{landlord_id}",
            json={"phone": "351-5555555", "email": "nuevo@example.com"},
            headers=admin["headers"],
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["phone"] == "351-5555555"
        assert Decimal(data["commission_pct"]) == Decimal("10.00")

    async def test_ca_02_02_admin_changing_commission_pct_returns_403_forbidden(self, client, seed):
        _org, owner, admin = await _seed_org_with_owner_and_admin(seed)
        created = await client.post(
            "/v1/landlords",
            json={"name": "Propietario", "commission_pct": "10.00"},
            headers=owner["headers"],
        )
        landlord_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/landlords/{landlord_id}",
            json={"commission_pct": "15.00"},
            headers=admin["headers"],
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

        # El % no debe haber cambiado.
        get_response = await client.get(f"/v1/landlords/{landlord_id}", headers=owner["headers"])
        assert Decimal(get_response.json()["data"]["commission_pct"]) == Decimal("10.00")

    async def test_ca_02_02_owner_changes_commission_pct_and_it_gets_audited(self, client, seed):
        org, owner, _admin = await _seed_org_with_owner_and_admin(seed)
        created = await client.post(
            "/v1/landlords",
            json={"name": "Propietario", "commission_pct": "10.00"},
            headers=owner["headers"],
        )
        landlord_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/landlords/{landlord_id}",
            json={"commission_pct": "12.50"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        assert Decimal(response.json()["data"]["commission_pct"]) == Decimal("12.50")

        rows = await seed.audit_rows(org["organization_id"], "landlord.commission_pct_changed")
        assert len(rows) == 1
        assert str(rows[0]["entity_id"]) == str(landlord_id)
        assert str(rows[0]["user_id"]) == str(owner["id"])
        assert Decimal(rows[0]["before_state"]["commission_pct"]) == Decimal("10.00")
        assert Decimal(rows[0]["after_state"]["commission_pct"]) == Decimal("12.50")

    async def test_owner_resubmitting_same_commission_pct_does_not_audit_again(self, client, seed):
        """Complementario a CA-02-02: si el owner reenvia el mismo valor
        vigente, no hay cambio real -- no debe generar una fila de
        auditoria adicional (mismo criterio que `settings.changed` en
        `modules/administracion`)."""
        org, owner, _admin = await _seed_org_with_owner_and_admin(seed)
        created = await client.post(
            "/v1/landlords",
            json={"name": "Propietario", "commission_pct": "10.00"},
            headers=owner["headers"],
        )
        landlord_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/landlords/{landlord_id}",
            json={"commission_pct": "10.00"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        rows = await seed.audit_rows(org["organization_id"], "landlord.commission_pct_changed")
        assert rows == []


class TestCA0203CommissionPctChangeDoesNotAffectPastData:
    """CA-02-03: el cambio de % de comision no altera liquidaciones ya
    generadas: la liquidacion siguiente usa el % nuevo (verificable por
    `commission_pct_used`).

    El modulo de Liquidaciones (issue #15+) todavia no existe -- no hay
    `commission_pct_used` que verificar en este PR. Este test cubre la
    parte de la invariante que SI es exigible aca: el valor de
    `commission_pct` que queda persistido en el landlord es siempre el
    ULTIMO establecido por el owner, listo para que el futuro modulo de
    liquidaciones lo snapshotee como `commission_pct_used` al generar.
    """

    async def test_ca_02_03_landlord_commission_pct_reflects_latest_value(self, client, seed):
        _org, owner, _admin = await _seed_org_with_owner_and_admin(seed)
        created = await client.post(
            "/v1/landlords",
            json={"name": "Propietario", "commission_pct": "10.00"},
            headers=owner["headers"],
        )
        landlord_id = created.json()["data"]["id"]

        await client.patch(
            f"/v1/landlords/{landlord_id}",
            json={"commission_pct": "20.00"},
            headers=owner["headers"],
        )

        response = await client.get(f"/v1/landlords/{landlord_id}", headers=owner["headers"])
        assert Decimal(response.json()["data"]["commission_pct"]) == Decimal("20.00")
