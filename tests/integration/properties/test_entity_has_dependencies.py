"""tests/integration/properties/test_entity_has_dependencies.py

SDD: docs/sdd/features/spec_module_01_propiedades.md RF-01 (v1.4,
issue #124: RN-D05), RF-05.
Implements: CA-01-03, CA-01-07, CA-01-12, CA-01-13, CA-01-14.
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


class TestCA0103DeleteWithoutActiveContractSoftDeletes:
    """CA-01-03 (v1.4, issue #124 -- antes devolvia 409
    ENTITY_HAS_DEPENDENCIES): Intentar borrar una propiedad con contrato
    activo devuelve 422 ENTITY_HAS_ACTIVE_CONTRACT; sin contrato activo,
    la baja es logica y la propiedad conserva su historial."""

    async def test_ca_01_03_delete_property_without_active_contract_returns_204(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "Sin contrato activo",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]

        response = await client.delete(f"/v1/properties/{property_id}", headers=owner["headers"])

        assert response.status_code == 204

    async def test_ca_01_03_deleted_property_disappears_from_listing_but_history_intact(
        self, client, seed
    ):
        """ "la propiedad conserva su historial" -- verificado indirectamente:
        el soft delete no destruye la fila (sigue existiendo en DB,
        solo `deleted_at` se completa); el GET posterior es 404 (RN-D01/RN-D02:
        borrado se trata igual que "no existe" para el resto de la API)."""
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "Con historial conservado",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]

        await client.delete(f"/v1/properties/{property_id}", headers=owner["headers"])

        listing = await client.get("/v1/properties", headers=owner["headers"])
        ids = {item["id"] for item in listing.json()["data"]}
        assert property_id not in ids

        detail = await client.get(f"/v1/properties/{property_id}", headers=owner["headers"])
        assert detail.status_code == 404

    async def test_delete_already_deleted_property_returns_404_not_409(self, client, seed):
        """Complementario: una segunda baja sobre el mismo recurso es
        404 (ya no existe, RN-D01/RN-D02), nunca 409."""
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "Doble baja",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]
        await client.delete(f"/v1/properties/{property_id}", headers=owner["headers"])

        second_delete = await client.delete(
            f"/v1/properties/{property_id}", headers=owner["headers"]
        )

        assert second_delete.status_code == 404
        assert second_delete.json()["error"]["code"] == "NOT_FOUND"

    async def test_ca_01_12_delete_property_with_active_contract_returns_422(self, client, seed):
        """CA-01-12 (issue #124, RN-D05): `DELETE /properties/:id` con un
        contrato `active` devuelve `422 ENTITY_HAS_ACTIVE_CONTRACT` con
        `details.entity_type = "property"`, `details.entity_id` y
        `details.active_contracts[]` (cada item con `contract_id`,
        `property_address`, `renter_name`, `start_date`, `end_date`)."""
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "Con contrato activo",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]
        renter_id = await seed.create_renter_row(organization_id=owner["organization_id"])
        contract = await client.post(
            "/v1/contracts",
            json={
                "property_id": property_id,
                "renter_id": str(renter_id),
                "currency": "ARS",
                "initial_amount": "1000.00",
                "start_date": "2026-01-01",
                "end_date": "2027-01-01",
                "daily_late_fee_pct": "0.1",
            },
            headers=owner["headers"],
        )
        contract_id = contract.json()["data"]["id"]
        await client.post(f"/v1/contracts/{contract_id}/activate", headers=owner["headers"])

        response = await client.delete(f"/v1/properties/{property_id}", headers=owner["headers"])

        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "ENTITY_HAS_ACTIVE_CONTRACT"
        details = body["error"]["details"]
        assert details["entity_type"] == "property"
        assert details["entity_id"] == property_id
        assert len(details["active_contracts"]) == 1
        ref = details["active_contracts"][0]
        assert ref["contract_id"] == contract_id
        assert ref["property_address"] == "Con contrato activo"
        assert ref["renter_name"] == "Inquilino de prueba"
        assert ref["start_date"] == "2026-01-01"
        assert ref["end_date"] == "2027-01-01"

    async def test_ca_01_12_terminated_contract_does_not_block_property_delete(self, client, seed):
        """CA-01-12: un contrato `terminated`/`expired`/`draft` NO bloquea
        la baja -- solo `active` cuenta (RN-D05)."""
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        renter_id = await seed.create_renter_row(organization_id=owner["organization_id"])
        property_id = await seed.create_property_row(
            organization_id=owner["organization_id"], landlord_id=landlord_id
        )
        await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            status="terminated",
        )

        response = await client.delete(f"/v1/properties/{property_id}", headers=owner["headers"])

        assert response.status_code == 204

    async def test_ca_01_13_delete_is_audited_and_property_not_eligible_for_new_contract(
        self, client, seed
    ):
        """CA-01-13 (issue #124, RN-D05): la baja logica queda auditada
        (`property.deleted`) y la propiedad eliminada deja de ser
        elegible: `POST /contracts` que la referencia devuelve 404."""
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        renter_id = await seed.create_renter_row(organization_id=owner["organization_id"])
        property_id = await seed.create_property_row(
            organization_id=owner["organization_id"], landlord_id=landlord_id
        )

        deleted = await client.delete(f"/v1/properties/{property_id}", headers=owner["headers"])
        assert deleted.status_code == 204

        rows = await seed.audit_rows(owner["organization_id"], "property.deleted")
        assert len(rows) == 1
        assert str(rows[0]["entity_id"]) == str(property_id)
        assert str(rows[0]["user_id"]) == str(owner["id"])

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
        assert new_contract.json()["error"]["field"] == "property_id"

    async def test_ca_01_14_historic_contract_still_shows_property_address(self, client, seed):
        """CA-01-14 (issue #124, RN-D05): tras la baja logica, los
        contratos historicos de la propiedad siguen exponiendo
        `property_address` (RN-12: la resolucion de display no filtra
        `deleted_at`)."""
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        renter_id = await seed.create_renter_row(organization_id=owner["organization_id"])
        property_id = await seed.create_property_row(
            organization_id=owner["organization_id"],
            landlord_id=landlord_id,
            address="Direccion historica 742",
        )
        contract_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            status="terminated",
        )

        await client.delete(f"/v1/properties/{property_id}", headers=owner["headers"])

        detail = await client.get(f"/v1/contracts/{contract_id}", headers=owner["headers"])
        assert detail.status_code == 200
        assert detail.json()["data"]["property_address"] == "Direccion historica 742"


class TestHasActiveDependenciesExtensibilityDocumented:
    """Verifica a nivel de repository (no HTTP) que el chequeo -- ya real
    desde el issue #17 -- distingue correctamente propiedades con y sin
    contrato `active`."""

    async def test_property_without_active_contract_has_no_active_dependencies(self, seed):
        from adminprop.db.session import get_session_factory
        from adminprop.modules.properties.repository import PropertyRepository

        org = await seed.create_organization_with_system_roles()
        landlord_id = await seed.create_landlord_row(organization_id=org["organization_id"])
        property_id = await seed.create_property_row(
            organization_id=org["organization_id"], landlord_id=landlord_id
        )

        session_factory = get_session_factory()
        async with session_factory() as session:
            repo = PropertyRepository(session)
            result = await repo.has_active_dependencies(property_id, org["organization_id"])

        assert result is False

    async def test_property_with_active_contract_has_active_dependencies(self, seed):
        from adminprop.db.session import get_session_factory
        from adminprop.modules.properties.repository import PropertyRepository

        org = await seed.create_organization_with_system_roles()
        landlord_id = await seed.create_landlord_row(organization_id=org["organization_id"])
        property_id = await seed.create_property_row(
            organization_id=org["organization_id"], landlord_id=landlord_id
        )
        renter_id = await seed.create_renter_row(organization_id=org["organization_id"])

        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            import sqlalchemy as sa

            await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
            await session.execute(
                sa.text(
                    "INSERT INTO contracts (organization_id, property_id, renter_id, currency, "
                    "initial_amount, current_amount, start_date, end_date, daily_late_fee_pct, "
                    "status) VALUES (:org_id, :property_id, :renter_id, 'ARS', 1000, 1000, "
                    "'2026-01-01', '2027-01-01', 0.1, 'active')"
                ),
                {
                    "org_id": str(org["organization_id"]),
                    "property_id": str(property_id),
                    "renter_id": str(renter_id),
                },
            )

        session_factory = get_session_factory()
        async with session_factory() as session:
            # issue #42: llamada directa a repositorio (sin HTTP/middleware
            # de tenant) -- setear el contexto explicitamente, igual que lo
            # haria `get_tenant_db_session` en produccion.
            from adminprop.db.session import set_tenant_context

            await set_tenant_context(session, org["organization_id"])
            repo = PropertyRepository(session)
            result = await repo.has_active_dependencies(property_id, org["organization_id"])

        assert result is True


class TestCA0107NeighborhoodEntityHasDependencies:
    """CA-01-07 (issue #99): borrar un barrio con propiedades asociadas
    devuelve 409 ENTITY_HAS_DEPENDENCIES; sin propiedades, la baja es
    logica."""

    async def test_delete_neighborhood_without_properties_returns_204(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )

        response = await client.delete(
            f"/v1/neighborhoods/{neighborhood_id}", headers=owner["headers"]
        )

        assert response.status_code == 204

    async def test_delete_neighborhood_with_properties_returns_409(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        await client.post(
            "/v1/properties",
            json={
                "address": "Depende del barrio",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )

        response = await client.delete(
            f"/v1/neighborhoods/{neighborhood_id}", headers=owner["headers"]
        )

        assert response.status_code == 409
        body = response.json()
        assert body["error"]["code"] == "ENTITY_HAS_DEPENDENCIES"
        assert body["error"]["details"]["entity_type"] == "neighborhood"

    async def test_delete_neighborhood_after_property_soft_deleted_returns_204(self, client, seed):
        """Una propiedad borrada (soft delete) ya no cuenta como
        dependencia -- el barrio queda libre para borrarse."""
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "Barrio a liberar",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]
        await client.delete(f"/v1/properties/{property_id}", headers=owner["headers"])

        response = await client.delete(
            f"/v1/neighborhoods/{neighborhood_id}", headers=owner["headers"]
        )

        assert response.status_code == 204

    async def test_delete_nonexistent_neighborhood_returns_404(self, client, seed):
        import uuid

        _org, owner = await _seed_org_with_owner(seed)

        response = await client.delete(
            f"/v1/neighborhoods/{uuid.uuid4()}", headers=owner["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
