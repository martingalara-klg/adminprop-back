"""tests/integration/properties/test_properties_crud.py

SDD: docs/sdd/features/spec_module_01_propiedades.md RF-01, RF-04, RF-05.
Implements: CA-01-01, CA-01-07, CA-01-08, CA-01-09.
"""

from __future__ import annotations

import uuid

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


class TestCA0101CreatePropertyAppearsInListingAndLandlordFicha:
    """CA-01-01: Se crea una propiedad con direccion, propietario y tipo;
    aparece en el listado y en la ficha del propietario."""

    async def test_ca_01_01_create_property_with_address_landlord_and_type(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )

        response = await client.post(
            "/v1/properties",
            json={
                "address": "Av. Colon 1234, Cordoba",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
                "property_type": "departamento",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["address"] == "Av. Colon 1234, Cordoba"
        assert data["landlord_id"] == str(landlord_id)
        assert data["property_type"] == "departamento"
        assert data["status"] == "available"

    async def test_ca_01_01_created_property_appears_in_listing(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "Calle Unica 111",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]

        response = await client.get("/v1/properties", headers=owner["headers"])

        assert response.status_code == 200
        ids = {item["id"] for item in response.json()["data"]}
        assert property_id in ids

    async def test_ca_01_01_created_property_appears_in_landlord_ficha(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "Calle De La Ficha 222",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]

        response = await client.get(f"/v1/landlords/{landlord_id}", headers=owner["headers"])

        assert response.status_code == 200
        properties = response.json()["data"]["properties"]
        assert any(p["id"] == property_id for p in properties)

    async def test_create_property_with_nonexistent_landlord_returns_404(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )

        response = await client.post(
            "/v1/properties",
            json={
                "address": "Sin Propietario 000",
                "landlord_id": str(uuid.uuid4()),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
        assert response.json()["error"]["field"] == "landlord_id"


class TestCA0108NeighborhoodRequiredOnProperties:
    """CA-01-08 (issue #99): `neighborhood_id` obligatorio en create/update;
    propiedades legacy sin barrio siguen legibles."""

    async def test_create_property_without_neighborhood_id_returns_400(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])

        response = await client.post(
            "/v1/properties",
            json={"address": "Sin barrio 1", "landlord_id": str(landlord_id)},
            headers=owner["headers"],
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_create_property_with_nonexistent_neighborhood_returns_404(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])

        response = await client.post(
            "/v1/properties",
            json={
                "address": "Barrio inexistente",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(uuid.uuid4()),
            },
            headers=owner["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
        assert response.json()["error"]["field"] == "neighborhood_id"

    async def test_legacy_property_without_neighborhood_is_still_readable(self, client, seed):
        """`neighborhood_id` es NULL en DB para propiedades preexistentes
        a issue #99 -- siguen siendo legibles en listado y ficha, con
        `neighborhood: null`."""
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        legacy_property_id = await seed.create_property_row(
            organization_id=owner["organization_id"],
            landlord_id=landlord_id,
            address="Propiedad legacy sin barrio",
        )

        listing = await client.get("/v1/properties", headers=owner["headers"])
        assert listing.status_code == 200
        item = next(p for p in listing.json()["data"] if p["id"] == str(legacy_property_id))
        assert item["neighborhood_id"] is None
        assert item["neighborhood"] is None

        detail = await client.get(f"/v1/properties/{legacy_property_id}", headers=owner["headers"])
        assert detail.status_code == 200
        assert detail.json()["data"]["neighborhood_id"] is None
        assert detail.json()["data"]["neighborhood"] is None


class TestPropertyListFilters:
    """RF-01: "Listado con filtros: propietario, estado, tipo, barrio;
    busqueda por direccion"."""

    async def test_filter_by_landlord_id(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        landlord_a = await seed.create_landlord_row(
            organization_id=owner["organization_id"], name="Landlord A"
        )
        landlord_b = await seed.create_landlord_row(
            organization_id=owner["organization_id"], name="Landlord B"
        )
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        await client.post(
            "/v1/properties",
            json={
                "address": "Direccion De A",
                "landlord_id": str(landlord_a),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )
        await client.post(
            "/v1/properties",
            json={
                "address": "Direccion De B",
                "landlord_id": str(landlord_b),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )

        response = await client.get(
            "/v1/properties", params={"landlord_id": str(landlord_a)}, headers=owner["headers"]
        )

        addresses = {item["address"] for item in response.json()["data"]}
        assert addresses == {"Direccion De A"}

    async def test_filter_by_neighborhood_id(self, client, seed):
        """CA-01-09 (issue #99): `?neighborhood_id=` devuelve solo las
        propiedades de ese barrio."""
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_a = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"], name="Nueva Cordoba"
        )
        neighborhood_b = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"], name="Guemes"
        )
        await client.post(
            "/v1/properties",
            json={
                "address": "En Nueva Cordoba",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_a),
            },
            headers=owner["headers"],
        )
        await client.post(
            "/v1/properties",
            json={
                "address": "En Guemes",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_b),
            },
            headers=owner["headers"],
        )

        response = await client.get(
            "/v1/properties",
            params={"neighborhood_id": str(neighborhood_a)},
            headers=owner["headers"],
        )

        addresses = {item["address"] for item in response.json()["data"]}
        assert addresses == {"En Nueva Cordoba"}

    async def test_filter_by_status(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "En refaccion filtro",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]
        await client.patch(
            f"/v1/properties/{property_id}",
            json={"status": "unavailable"},
            headers=owner["headers"],
        )
        await client.post(
            "/v1/properties",
            json={
                "address": "Disponible filtro",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )

        response = await client.get(
            "/v1/properties", params={"status": "unavailable"}, headers=owner["headers"]
        )

        addresses = {item["address"] for item in response.json()["data"]}
        assert addresses == {"En refaccion filtro"}

    async def test_filter_by_property_type(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        await client.post(
            "/v1/properties",
            json={
                "address": "Cochera filtro",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
                "property_type": "cochera",
            },
            headers=owner["headers"],
        )
        await client.post(
            "/v1/properties",
            json={
                "address": "Local filtro",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
                "property_type": "local",
            },
            headers=owner["headers"],
        )

        response = await client.get(
            "/v1/properties", params={"property_type": "cochera"}, headers=owner["headers"]
        )

        addresses = {item["address"] for item in response.json()["data"]}
        assert addresses == {"Cochera filtro"}

    async def test_list_properties_paginates_with_cursor(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        for i in range(3):
            await client.post(
                "/v1/properties",
                json={
                    "address": f"Direccion paginada {i}",
                    "landlord_id": str(landlord_id),
                    "neighborhood_id": str(neighborhood_id),
                },
                headers=owner["headers"],
            )

        first_page = await client.get(
            "/v1/properties", params={"limit": 2}, headers=owner["headers"]
        )
        assert len(first_page.json()["data"]) == 2
        next_cursor = first_page.json()["meta"]["next_cursor"]
        assert next_cursor is not None

        second_page = await client.get(
            "/v1/properties",
            params={"limit": 2, "cursor": next_cursor},
            headers=owner["headers"],
        )
        assert len(second_page.json()["data"]) == 1
        first_ids = {item["id"] for item in first_page.json()["data"]}
        second_ids = {item["id"] for item in second_page.json()["data"]}
        assert first_ids.isdisjoint(second_ids)

    async def test_search_by_address_substring(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        await client.post(
            "/v1/properties",
            json={
                "address": "Bulevar San Juan 500",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )
        await client.post(
            "/v1/properties",
            json={
                "address": "Otra direccion 900",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )

        response = await client.get(
            "/v1/properties", params={"search": "San Juan"}, headers=owner["headers"]
        )

        addresses = {item["address"] for item in response.json()["data"]}
        assert addresses == {"Bulevar San Juan 500"}


class TestPropertyUpdate:
    """RF-01: "Edicion de todos los campos salvo el estado rented"."""

    async def test_update_address_and_notes(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "Direccion Original 1",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/properties/{property_id}",
            json={"address": "Direccion Nueva 2", "notes": "Actualizado"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        assert response.json()["data"]["address"] == "Direccion Nueva 2"

    async def test_update_status_to_unavailable(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "En refaccion 3",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/properties/{property_id}",
            json={"status": "unavailable"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        assert response.json()["data"]["status"] == "unavailable"

    async def test_update_status_to_rented_is_rejected_rf04(self, client, seed):
        """RF-04: `rented` es derivado (solo lo setea el modulo de
        contratos, issue #17) -- el cliente NUNCA puede setearlo
        directamente via PATCH."""
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "No debe alquilarse manualmente 4",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/properties/{property_id}",
            json={"status": "rented"},
            headers=owner["headers"],
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_update_property_type(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "Cambia de tipo 8",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/properties/{property_id}",
            json={"property_type": "cochera"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        assert response.json()["data"]["property_type"] == "cochera"

    async def test_update_landlord_id_to_nonexistent_returns_404(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "Cambio de dueno 5",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/properties/{property_id}",
            json={"landlord_id": str(uuid.uuid4())},
            headers=owner["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_update_neighborhood_id_to_nonexistent_returns_404(self, client, seed):
        """CA-01-08 (issue #99): mismo criterio RN-D01 que `landlord_id`."""
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "Cambio de barrio invalido",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/properties/{property_id}",
            json={"neighborhood_id": str(uuid.uuid4())},
            headers=owner["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
        assert response.json()["error"]["field"] == "neighborhood_id"

    async def test_update_neighborhood_id_to_null_is_rejected(self, client, seed):
        """CA-01-08: el barrio no puede vaciarse via PATCH (obligatorio
        de ahora en mas, aunque venga en el body)."""
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "No se puede vaciar el barrio",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/properties/{property_id}",
            json={"neighborhood_id": None},
            headers=owner["headers"],
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_update_neighborhood_id_changes_embedded_neighborhood(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_a = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"], name="Barrio Original"
        )
        neighborhood_b = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"], name="Barrio Nuevo"
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "Cambia de barrio 9",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_a),
            },
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/properties/{property_id}",
            json={"neighborhood_id": str(neighborhood_b)},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        assert response.json()["data"]["neighborhood_id"] == str(neighborhood_b)

    async def test_update_landlord_id_is_audited(self, client, seed):
        """RN: "Cambiar el propietario de una propiedad ... es una
        operacion auditada"."""
        _org, owner = await _seed_org_with_owner(seed)
        landlord_a = await seed.create_landlord_row(
            organization_id=owner["organization_id"], name="Dueno original"
        )
        landlord_b = await seed.create_landlord_row(
            organization_id=owner["organization_id"], name="Dueno nuevo"
        )
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "Cambia de dueno 6",
                "landlord_id": str(landlord_a),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/properties/{property_id}",
            json={"landlord_id": str(landlord_b)},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        assert response.json()["data"]["landlord_id"] == str(landlord_b)
        rows = await seed.audit_rows(owner["organization_id"], "property.landlord_changed")
        assert any(str(r["entity_id"]) == property_id for r in rows)


class TestPropertyFicha:
    """RF-03: ficha consolidada -- datos + cuentas de servicio; contrato
    vigente, historial de reparaciones y conceptos recurrentes quedan
    como placeholders declarados (issues #17, #26, #28)."""

    async def test_ficha_includes_placeholders_for_future_modules(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=owner["organization_id"]
        )
        created = await client.post(
            "/v1/properties",
            json={
                "address": "Ficha completa 7",
                "landlord_id": str(landlord_id),
                "neighborhood_id": str(neighborhood_id),
            },
            headers=owner["headers"],
        )
        property_id = created.json()["data"]["id"]

        response = await client.get(f"/v1/properties/{property_id}", headers=owner["headers"])

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["service_accounts"] == []
        assert data["active_contract"] is None
        assert data["work_orders_history"] == []
        assert data["recurring_charges"] == []

    async def test_get_nonexistent_property_returns_404(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.get(f"/v1/properties/{uuid.uuid4()}", headers=owner["headers"])

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
