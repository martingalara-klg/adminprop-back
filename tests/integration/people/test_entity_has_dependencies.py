"""tests/integration/people/test_entity_has_dependencies.py

SDD: docs/sdd/features/spec_module_02_personas.md RF-01/RF-03 (v1.1,
issue #124: RN-D05).
Implements: CA-02-06, CA-02-08, CA-02-09.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def _seed_org_with_owner(seed):
    org = await seed.create_organization_with_system_roles()
    owner = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["owner"],
        role_name="owner",
    )
    return org, owner


class TestCA0206DeleteWithoutDependenciesSoftDeletes:
    """CA-02-06 (mitad "sin dependencias"): sin dependencias, la baja es
    logica. La mitad inquilino "con contrato `active`" cambio en el issue
    #124 (RN-D05): es 422 ENTITY_HAS_ACTIVE_CONTRACT -- ver
    `TestCA0208RenterWithActiveContractBlocked` abajo. La mitad landlord
    "con propiedades activas -> 409" sigue con el chequeo extensible
    (`LandlordRepository.has_active_dependencies`, `False` hoy -- fuera
    del alcance del issue #124)."""

    async def test_ca_02_06_delete_landlord_without_properties_returns_204(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        created = await client.post(
            "/v1/landlords",
            json={"name": "Sin dependencias", "commission_pct": "10"},
            headers=owner["headers"],
        )
        landlord_id = created.json()["data"]["id"]

        response = await client.delete(f"/v1/landlords/{landlord_id}", headers=owner["headers"])

        assert response.status_code == 204

    async def test_ca_02_06_delete_renter_without_contracts_returns_204(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        created = await client.post(
            "/v1/renters", json={"name": "Sin contrato"}, headers=owner["headers"]
        )
        renter_id = created.json()["data"]["id"]

        response = await client.delete(f"/v1/renters/{renter_id}", headers=owner["headers"])

        assert response.status_code == 204

    async def test_delete_already_deleted_landlord_returns_404_not_409(self, client, seed):
        """Complementario: una segunda baja sobre el mismo recurso es
        404 (ya no existe, RN-D01/RN-D02), nunca 409."""
        _org, owner = await _seed_org_with_owner(seed)
        created = await client.post(
            "/v1/landlords",
            json={"name": "Doble baja", "commission_pct": "10"},
            headers=owner["headers"],
        )
        landlord_id = created.json()["data"]["id"]
        await client.delete(f"/v1/landlords/{landlord_id}", headers=owner["headers"])

        second_delete = await client.delete(
            f"/v1/landlords/{landlord_id}", headers=owner["headers"]
        )

        assert second_delete.status_code == 404
        assert second_delete.json()["error"]["code"] == "NOT_FOUND"


class TestHasActiveDependenciesExtensibilityDocumented:
    """Verifica a nivel de repository (no HTTP) que el chequeo extensible
    existe con la firma correcta y hoy siempre retorna `False` --
    documenta explicitamente el alcance actual de CA-02-06 (issue #13)
    de forma independiente del flujo HTTP de arriba."""

    async def test_landlord_has_active_dependencies_is_always_false_today(self, seed):
        from adminprop.db.session import get_session_factory
        from adminprop.modules.people.repository import LandlordRepository

        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])

        session_factory = get_session_factory()
        async with session_factory() as session:
            repo = LandlordRepository(session)
            result = await repo.has_active_dependencies(landlord_id, owner["organization_id"])

        assert result is False

    async def test_renter_list_active_contracts_is_empty_without_contracts(self, seed):
        """Issue #124 (RN-D05): `RenterRepository.list_active_contracts`
        reemplaza al placeholder `has_active_dependencies` (siempre
        `False` desde el issue #13) -- sin contratos, la lista es vacia
        y la baja procede."""
        from adminprop.db.session import get_session_factory, set_tenant_context
        from adminprop.modules.people.repository import RenterRepository

        _org, owner = await _seed_org_with_owner(seed)
        renter_id = await seed.create_renter_row(organization_id=owner["organization_id"])

        session_factory = get_session_factory()
        async with session_factory() as session:
            await set_tenant_context(session, owner["organization_id"])
            repo = RenterRepository(session)
            result = await repo.list_active_contracts(renter_id, owner["organization_id"])

        assert result == []


class TestCA0208RenterWithActiveContractBlocked:
    """CA-02-08 (issue #124, RN-D05): `DELETE /renters/:id` con un
    contrato `active` devuelve `422 ENTITY_HAS_ACTIVE_CONTRACT` con
    `details.entity_type = "renter"`, `details.entity_id` y
    `details.active_contracts[]`; un contrato `terminated`/`expired`/
    `draft` no bloquea la baja."""

    async def test_ca_02_08_delete_renter_with_active_contract_returns_422(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        property_id = await seed.create_property_row(
            organization_id=owner["organization_id"],
            landlord_id=landlord_id,
            address="Bloqueada por contrato 1",
        )
        renter_id = await seed.create_renter_row(organization_id=owner["organization_id"])
        contract_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            status="active",
        )

        response = await client.delete(f"/v1/renters/{renter_id}", headers=owner["headers"])

        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "ENTITY_HAS_ACTIVE_CONTRACT"
        details = body["error"]["details"]
        assert details["entity_type"] == "renter"
        assert details["entity_id"] == str(renter_id)
        assert len(details["active_contracts"]) == 1
        ref = details["active_contracts"][0]
        assert ref["contract_id"] == str(contract_id)
        assert ref["property_address"] == "Bloqueada por contrato 1"
        assert ref["renter_id"] == str(renter_id)
        # El inquilino sigue existiendo (la baja no se aplico).
        detail = await client.get(f"/v1/renters/{renter_id}", headers=owner["headers"])
        assert detail.status_code == 200

    async def test_ca_02_08_terminated_contract_does_not_block_renter_delete(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        property_id = await seed.create_property_row(
            organization_id=owner["organization_id"], landlord_id=landlord_id
        )
        renter_id = await seed.create_renter_row(organization_id=owner["organization_id"])
        await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            status="terminated",
        )

        response = await client.delete(f"/v1/renters/{renter_id}", headers=owner["headers"])

        assert response.status_code == 204


class TestCA0209RenterSoftDeleteAuditedAndExcluded:
    """CA-02-09 (issue #124, RN-D05): la baja logica de un inquilino
    queda auditada (`renter.deleted`); el inquilino desaparece de
    `GET /renters`, su `GET /renters/:id` devuelve 404, `POST /contracts`
    que lo referencie devuelve 404 NOT_FOUND, y sus contratos historicos
    siguen exponiendo `renter_name` (RN-12)."""

    async def test_ca_02_09_delete_is_audited_and_renter_excluded_from_selects(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        property_id = await seed.create_property_row(
            organization_id=owner["organization_id"], landlord_id=landlord_id, status="available"
        )
        renter_id = await seed.create_renter_row(organization_id=owner["organization_id"])

        deleted = await client.delete(f"/v1/renters/{renter_id}", headers=owner["headers"])
        assert deleted.status_code == 204

        rows = await seed.audit_rows(owner["organization_id"], "renter.deleted")
        assert len(rows) == 1
        assert str(rows[0]["entity_id"]) == str(renter_id)
        assert str(rows[0]["user_id"]) == str(owner["id"])

        listing = await client.get("/v1/renters", headers=owner["headers"])
        assert str(renter_id) not in {item["id"] for item in listing.json()["data"]}

        detail = await client.get(f"/v1/renters/{renter_id}", headers=owner["headers"])
        assert detail.status_code == 404
        debt = await client.get(f"/v1/renters/{renter_id}/debt", headers=owner["headers"])
        assert debt.status_code == 404

        # RN-06: un inquilino eliminado no es elegible para contrato nuevo.
        new_contract = await client.post(
            "/v1/contracts",
            json={
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "currency": "ARS",
                "initial_amount": "1000.00",
                "start_date": "2026-08-01",
                "end_date": "2027-08-01",
                "daily_late_fee_pct": "0.1",
            },
            headers=owner["headers"],
        )
        assert new_contract.status_code == 404
        assert new_contract.json()["error"]["field"] == "renter_id"

    async def test_ca_02_09_historic_contract_still_shows_renter_name(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        property_id = await seed.create_property_row(
            organization_id=owner["organization_id"], landlord_id=landlord_id
        )
        renter_id = await seed.create_renter_row(
            organization_id=owner["organization_id"], name="Inquilino historico"
        )
        contract_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            status="terminated",
        )

        await client.delete(f"/v1/renters/{renter_id}", headers=owner["headers"])

        detail = await client.get(f"/v1/contracts/{contract_id}", headers=owner["headers"])
        assert detail.status_code == 200
        assert detail.json()["data"]["renter_name"] == "Inquilino historico"
