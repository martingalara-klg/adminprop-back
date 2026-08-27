"""tests/integration/properties/test_service_accounts.py

SDD: docs/sdd/features/spec_module_01_propiedades.md RF-02.
Implements: CA-01-02.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _seed_property(seed, client, owner):
    landlord_id = await seed.create_landlord_row(organization_id=owner["organization_id"])
    neighborhood_id = await seed.create_neighborhood_row(organization_id=owner["organization_id"])
    created = await client.post(
        "/v1/properties",
        json={
            "address": "Propiedad con cuentas",
            "landlord_id": str(landlord_id),
            "neighborhood_id": str(neighborhood_id),
        },
        headers=owner["headers"],
    )
    return created.json()["data"]["id"]


async def _seed_org_with_owner(seed):
    org = await seed.create_organization_with_system_roles()
    owner = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["owner"],
        role_name="owner",
    )
    return org, owner


class TestCA0102ServiceAccountsAllVisibleTogetherInFicha:
    """CA-01-02: Se cargan las cuentas de rentas, muni, luz (con n° de
    cliente y n° de contrato), gas, agua y expensas de una propiedad y se
    ven todas juntas en su ficha."""

    async def test_ca_01_02_all_seven_service_types_visible_together_in_ficha(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id = await _seed_property(seed, client, owner)

        for service_type, account_number, secondary_number in (
            ("rentas", "RENTAS-001", None),
            ("municipalidad", "MUNI-002", None),
            ("luz", "CLIENTE-003", "CONTRATO-003"),
            ("gas", "GAS-004", None),
            ("agua", "AGUA-005", None),
            ("expensas", "EXP-006", None),
            ("otro", "OTRO-007", None),
        ):
            response = await client.post(
                f"/v1/properties/{property_id}/service-accounts",
                json={
                    "service_type": service_type,
                    "account_number": account_number,
                    "secondary_number": secondary_number,
                },
                headers=owner["headers"],
            )
            assert response.status_code == 201

        ficha = await client.get(f"/v1/properties/{property_id}", headers=owner["headers"])
        accounts = ficha.json()["data"]["service_accounts"]
        assert len(accounts) == 7
        by_type = {a["service_type"]: a for a in accounts}
        assert by_type["luz"]["account_number"] == "CLIENTE-003"
        assert by_type["luz"]["secondary_number"] == "CONTRATO-003"
        assert set(by_type.keys()) == {
            "rentas",
            "municipalidad",
            "luz",
            "gas",
            "agua",
            "expensas",
            "otro",
        }

    async def test_list_service_accounts_endpoint_returns_all(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id = await _seed_property(seed, client, owner)
        await client.post(
            f"/v1/properties/{property_id}/service-accounts",
            json={"service_type": "gas", "account_number": "GAS-100"},
            headers=owner["headers"],
        )

        response = await client.get(
            f"/v1/properties/{property_id}/service-accounts", headers=owner["headers"]
        )

        assert response.status_code == 200
        assert len(response.json()["data"]) == 1

    async def test_create_service_account_for_nonexistent_property_returns_404(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.post(
            f"/v1/properties/{uuid.uuid4()}/service-accounts",
            json={"service_type": "gas", "account_number": "GAS-999"},
            headers=owner["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_create_service_account_with_invalid_service_type_returns_400(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id = await _seed_property(seed, client, owner)

        response = await client.post(
            f"/v1/properties/{property_id}/service-accounts",
            json={"service_type": "cable", "account_number": "X-1"},
            headers=owner["headers"],
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


class TestServiceAccountUpdateAndDelete:
    async def test_update_service_account_number(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id = await _seed_property(seed, client, owner)
        created = await client.post(
            f"/v1/properties/{property_id}/service-accounts",
            json={"service_type": "agua", "account_number": "AGUA-OLD"},
            headers=owner["headers"],
        )
        account_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/service-accounts/{account_id}",
            json={"account_number": "AGUA-NEW"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        assert response.json()["data"]["account_number"] == "AGUA-NEW"

    async def test_update_service_account_secondary_number_and_notes(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id = await _seed_property(seed, client, owner)
        created = await client.post(
            f"/v1/properties/{property_id}/service-accounts",
            json={"service_type": "luz", "account_number": "CLIENTE-1"},
            headers=owner["headers"],
        )
        account_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/service-accounts/{account_id}",
            json={"secondary_number": "CONTRATO-9", "notes": "actualizado"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["secondary_number"] == "CONTRATO-9"
        assert data["notes"] == "actualizado"

    async def test_delete_service_account_removes_it_from_ficha(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id = await _seed_property(seed, client, owner)
        created = await client.post(
            f"/v1/properties/{property_id}/service-accounts",
            json={"service_type": "expensas", "account_number": "EXP-DEL"},
            headers=owner["headers"],
        )
        account_id = created.json()["data"]["id"]

        delete_response = await client.delete(
            f"/v1/service-accounts/{account_id}", headers=owner["headers"]
        )
        assert delete_response.status_code == 204

        ficha = await client.get(f"/v1/properties/{property_id}", headers=owner["headers"])
        assert ficha.json()["data"]["service_accounts"] == []

    async def test_update_nonexistent_service_account_returns_404(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.patch(
            f"/v1/service-accounts/{uuid.uuid4()}",
            json={"account_number": "X"},
            headers=owner["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_delete_nonexistent_service_account_returns_404(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.delete(
            f"/v1/service-accounts/{uuid.uuid4()}", headers=owner["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
