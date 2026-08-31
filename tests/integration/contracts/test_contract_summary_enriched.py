"""tests/integration/contracts/test_contract_summary_enriched.py

SDD: docs/sdd/features/spec_module_03_contratos.md RF-01 (listado
enriquecido) + core/sdd_03_api_contracts.md v1.16 §8 (issue #123,
decision #129, RN-12).
Implements: CA-03-31, CA-03-32, CA-03-33, CA-03-34, CA-03-35.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

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


def _current_month_payload(property_id, renter_id) -> dict:
    """Body de POST /v1/contracts con `start_date` en el mes actual --
    un alta normal, sin carga inicial ni cobros retroactivos (RN-11),
    para ejercitar el ciclo draft -> active -> terminated completo."""
    today = datetime.now(UTC).date()
    start = date(today.year, today.month, 1)
    end = date(start.year + 1, start.month, 1)
    return {
        "property_id": str(property_id),
        "renter_id": str(renter_id),
        "currency": "ARS",
        "initial_amount": "150000.00",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily_late_fee_pct": "0.5",
    }


_DISPLAY_FIELDS = ("property_address", "property_neighborhood", "renter_name")


class TestCA0331ListExposesDisplayFields:
    """CA-03-31 (issue #123, RN-12): cada item de `GET /contracts` expone
    `property_address` y `renter_name` con los valores de la propiedad y
    el inquilino del contrato, y `property_neighborhood` con el nombre
    del barrio de la propiedad -- resueltos en el mismo query del listado
    (JOIN, sin N+1)."""

    async def test_ca_03_31_list_items_expose_address_neighborhood_and_renter_name(
        self, client, seed
    ):
        _org, owner = await _seed_org_with_owner(seed)
        org_id = owner["organization_id"]
        landlord_id = await seed.create_landlord_row(organization_id=org_id)
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=org_id, name="Nueva Cordoba"
        )
        property_id = await seed.create_property_row(
            organization_id=org_id,
            landlord_id=landlord_id,
            address="Obispo Trejo 742",
            neighborhood_id=neighborhood_id,
        )
        renter_id = await seed.create_renter_row(organization_id=org_id, name="Juan Perez")
        contract_id = await seed.create_contract_row(
            organization_id=org_id, property_id=property_id, renter_id=renter_id
        )

        response = await client.get("/v1/contracts", headers=owner["headers"])

        assert response.status_code == 200
        items = response.json()["data"]
        item = next(i for i in items if i["id"] == str(contract_id))
        assert item["property_address"] == "Obispo Trejo 742"
        assert item["property_neighborhood"] == "Nueva Cordoba"
        assert item["renter_name"] == "Juan Perez"

    async def test_ca_03_31_two_contracts_resolve_their_own_references(self, client, seed):
        """Dos contratos con propiedad/inquilino/barrio distintos: cada
        item del listado resuelve SUS referencias (el JOIN no cruza
        filas)."""
        _org, owner = await _seed_org_with_owner(seed)
        org_id = owner["organization_id"]
        landlord_id = await seed.create_landlord_row(organization_id=org_id)

        neighborhood_a = await seed.create_neighborhood_row(organization_id=org_id, name="Centro")
        property_a = await seed.create_property_row(
            organization_id=org_id,
            landlord_id=landlord_id,
            address="San Martin 100",
            neighborhood_id=neighborhood_a,
        )
        renter_a = await seed.create_renter_row(organization_id=org_id, name="Ana Alvarez")
        contract_a = await seed.create_contract_row(
            organization_id=org_id, property_id=property_a, renter_id=renter_a
        )

        neighborhood_b = await seed.create_neighborhood_row(organization_id=org_id, name="Alberdi")
        property_b = await seed.create_property_row(
            organization_id=org_id,
            landlord_id=landlord_id,
            address="Colon 2500",
            neighborhood_id=neighborhood_b,
        )
        renter_b = await seed.create_renter_row(organization_id=org_id, name="Bruno Bustos")
        contract_b = await seed.create_contract_row(
            organization_id=org_id,
            property_id=property_b,
            renter_id=renter_b,
            start_date="2026-02-01",
            end_date="2027-02-01",
        )

        response = await client.get("/v1/contracts", headers=owner["headers"])

        assert response.status_code == 200
        by_id = {item["id"]: item for item in response.json()["data"]}
        item_a = by_id[str(contract_a)]
        assert item_a["property_address"] == "San Martin 100"
        assert item_a["property_neighborhood"] == "Centro"
        assert item_a["renter_name"] == "Ana Alvarez"
        item_b = by_id[str(contract_b)]
        assert item_b["property_address"] == "Colon 2500"
        assert item_b["property_neighborhood"] == "Alberdi"
        assert item_b["renter_name"] == "Bruno Bustos"


class TestCA0332PropertyWithoutNeighborhood:
    """CA-03-32 (issue #123, RN-12): un contrato cuya propiedad no tiene
    barrio asignado (`neighborhood_id` `NULL`) expone
    `property_neighborhood: null`, con `property_address` y `renter_name`
    igualmente poblados."""

    async def test_ca_03_32_property_without_neighborhood_exposes_null(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        org_id = owner["organization_id"]
        landlord_id = await seed.create_landlord_row(organization_id=org_id)
        property_id = await seed.create_property_row(
            organization_id=org_id, landlord_id=landlord_id, address="Belgrano 555"
        )
        renter_id = await seed.create_renter_row(organization_id=org_id, name="Carla Castro")
        contract_id = await seed.create_contract_row(
            organization_id=org_id, property_id=property_id, renter_id=renter_id
        )

        response = await client.get("/v1/contracts", headers=owner["headers"])

        assert response.status_code == 200
        item = next(i for i in response.json()["data"] if i["id"] == str(contract_id))
        assert item["property_neighborhood"] is None
        assert item["property_address"] == "Belgrano 555"
        assert item["renter_name"] == "Carla Castro"


class TestCA0333WriteEndpointsExposeDisplayFields:
    """CA-03-33 (issue #123, RN-12): las respuestas de `POST /contracts`,
    `PATCH /contracts/:id`, `POST /contracts/:id/activate` y
    `POST /contracts/:id/terminate` exponen los tres campos (mismo
    `ContractSummary`)."""

    async def test_ca_03_33_full_lifecycle_responses_expose_display_fields(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        org_id = owner["organization_id"]
        landlord_id = await seed.create_landlord_row(organization_id=org_id)
        neighborhood_id = await seed.create_neighborhood_row(
            organization_id=org_id, name="General Paz"
        )
        property_id = await seed.create_property_row(
            organization_id=org_id,
            landlord_id=landlord_id,
            address="24 de Septiembre 1300",
            neighborhood_id=neighborhood_id,
        )
        renter_id = await seed.create_renter_row(organization_id=org_id, name="Diego Diaz")

        def _assert_display(data: dict) -> None:
            assert data["property_address"] == "24 de Septiembre 1300"
            assert data["property_neighborhood"] == "General Paz"
            assert data["renter_name"] == "Diego Diaz"

        create_response = await client.post(
            "/v1/contracts",
            json=_current_month_payload(property_id, renter_id),
            headers=owner["headers"],
        )
        assert create_response.status_code == 201
        _assert_display(create_response.json()["data"])
        contract_id = create_response.json()["data"]["id"]

        patch_response = await client.patch(
            f"/v1/contracts/{contract_id}",
            json={"notes": "nota de prueba"},
            headers=owner["headers"],
        )
        assert patch_response.status_code == 200
        _assert_display(patch_response.json()["data"])

        activate_response = await client.post(
            f"/v1/contracts/{contract_id}/activate", headers=owner["headers"]
        )
        assert activate_response.status_code == 200
        _assert_display(activate_response.json()["data"])

        terminate_response = await client.post(
            f"/v1/contracts/{contract_id}/terminate",
            json={"reason": "rescision de prueba"},
            headers=owner["headers"],
        )
        assert terminate_response.status_code == 200
        _assert_display(terminate_response.json()["data"])


class TestCA0334DetailExposesDisplayFields:
    """CA-03-34 (issue #123, RN-12): `GET /contracts/:id` (`ContractDetail`)
    también expone los tres campos, junto con `monthly_amounts[]`."""

    async def test_ca_03_34_contract_detail_exposes_display_fields(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        org_id = owner["organization_id"]
        landlord_id = await seed.create_landlord_row(organization_id=org_id)
        neighborhood_id = await seed.create_neighborhood_row(organization_id=org_id, name="Guemes")
        property_id = await seed.create_property_row(
            organization_id=org_id,
            landlord_id=landlord_id,
            address="Achaval Rodriguez 250",
            neighborhood_id=neighborhood_id,
        )
        renter_id = await seed.create_renter_row(organization_id=org_id, name="Elena Escudero")
        contract_id = await seed.create_contract_row(
            organization_id=org_id, property_id=property_id, renter_id=renter_id
        )

        response = await client.get(f"/v1/contracts/{contract_id}", headers=owner["headers"])

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["property_address"] == "Achaval Rodriguez 250"
        assert data["property_neighborhood"] == "Guemes"
        assert data["renter_name"] == "Elena Escudero"
        assert "monthly_amounts" in data


class TestCA0335DisplayFieldsAreReadOnly:
    """CA-03-35 (issue #123, RN-12): enviar `property_address`,
    `property_neighborhood` o `renter_name` en el body de
    `POST /contracts` o `PATCH /contracts/:id` devuelve
    `400 VALIDATION_ERROR` (campos de solo lectura)."""

    @pytest.mark.parametrize("field", _DISPLAY_FIELDS)
    async def test_ca_03_35_post_with_display_field_returns_400(self, client, seed, field):
        _org, owner = await _seed_org_with_owner(seed)
        org_id = owner["organization_id"]
        landlord_id = await seed.create_landlord_row(organization_id=org_id)
        property_id = await seed.create_property_row(
            organization_id=org_id, landlord_id=landlord_id
        )
        renter_id = await seed.create_renter_row(organization_id=org_id)
        payload = _current_month_payload(property_id, renter_id)
        payload[field] = "no permitido"

        response = await client.post("/v1/contracts", json=payload, headers=owner["headers"])

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.parametrize("field", _DISPLAY_FIELDS)
    async def test_ca_03_35_patch_with_display_field_returns_400(self, client, seed, field):
        _org, owner = await _seed_org_with_owner(seed)
        org_id = owner["organization_id"]
        landlord_id = await seed.create_landlord_row(organization_id=org_id)
        property_id = await seed.create_property_row(
            organization_id=org_id, landlord_id=landlord_id
        )
        renter_id = await seed.create_renter_row(organization_id=org_id)
        contract_id = await seed.create_contract_row(
            organization_id=org_id, property_id=property_id, renter_id=renter_id
        )

        response = await client.patch(
            f"/v1/contracts/{contract_id}",
            json={field: "no permitido"},
            headers=owner["headers"],
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


class TestCrossTenantListStillIsolated:
    """RN-D01 (defense in depth del JOIN de RN-12): el listado de la
    organizacion A no expone contratos de la organizacion B aunque el
    query ahora una `properties`/`neighborhoods`/`renters`."""

    async def test_cross_tenant_contract_never_appears_in_list(self, client, seed):
        _org_a, owner_a = await _seed_org_with_owner(seed)

        org_b, _owner_b = await _seed_org_with_owner(seed)
        org_b_id = org_b["organization_id"]
        landlord_b = await seed.create_landlord_row(organization_id=org_b_id)
        neighborhood_b = await seed.create_neighborhood_row(
            organization_id=org_b_id, name="Barrio B"
        )
        property_b = await seed.create_property_row(
            organization_id=org_b_id, landlord_id=landlord_b, neighborhood_id=neighborhood_b
        )
        renter_b = await seed.create_renter_row(organization_id=org_b_id)
        contract_b = await seed.create_contract_row(
            organization_id=org_b_id, property_id=property_b, renter_id=renter_b
        )

        response = await client.get("/v1/contracts", headers=owner_a["headers"])

        assert response.status_code == 200
        assert str(contract_b) not in {item["id"] for item in response.json()["data"]}
