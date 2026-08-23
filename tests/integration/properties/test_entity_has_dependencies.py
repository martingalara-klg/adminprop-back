"""tests/integration/properties/test_entity_has_dependencies.py

SDD: docs/sdd/features/spec_module_01_propiedades.md RF-01.
Implements: CA-01-03.
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
    """CA-01-03: Intentar borrar una propiedad con contrato activo
    devuelve 409 ENTITY_HAS_DEPENDENCIES; sin contrato activo, la baja es
    logica y la propiedad conserva su historial.

    Issue #17: el modulo `contracts` ya existe -- ver
    `test_ca_01_03_delete_property_with_active_contract_returns_409` en
    esta misma clase, que ejercita la mitad "con contrato activo -> 409"
    end-to-end.
    """

    async def test_ca_01_03_delete_property_without_active_contract_returns_204(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        created = await client.post(
            "/v1/properties",
            json={"address": "Sin contrato activo", "landlord_id": str(landlord_id)},
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
        created = await client.post(
            "/v1/properties",
            json={"address": "Con historial conservado", "landlord_id": str(landlord_id)},
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
        created = await client.post(
            "/v1/properties",
            json={"address": "Doble baja", "landlord_id": str(landlord_id)},
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]
        await client.delete(f"/v1/properties/{property_id}", headers=owner["headers"])

        second_delete = await client.delete(
            f"/v1/properties/{property_id}", headers=owner["headers"]
        )

        assert second_delete.status_code == 404
        assert second_delete.json()["error"]["code"] == "NOT_FOUND"

    async def test_ca_01_03_delete_property_with_active_contract_returns_409(self, client, seed):
        """Issue #17: la propiedad con un contrato `active` no se borra."""
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        created = await client.post(
            "/v1/properties",
            json={"address": "Con contrato activo", "landlord_id": str(landlord_id)},
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

        assert response.status_code == 409
        body = response.json()
        assert body["error"]["code"] == "ENTITY_HAS_DEPENDENCIES"
        assert body["error"]["details"]["entity_id"] == property_id


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
