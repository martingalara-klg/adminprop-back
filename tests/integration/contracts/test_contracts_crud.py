"""tests/integration/contracts/test_contracts_crud.py

SDD: docs/sdd/features/spec_module_03_contratos.md RF-01..RF-03.
Implements: CA-03-01, CA-03-02, CA-03-03, CA-03-06, CA-03-08, CA-01-04.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_session_factory

pytestmark = pytest.mark.asyncio


async def _seed_org_with_owner(seed):
    org = await seed.create_organization_with_system_roles()
    owner = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["owner"],
        role_name="owner",
    )
    return org, owner


async def _seed_property_and_renter(seed, organization_id):
    landlord_id = await seed.create_landlord_row(organization_id=organization_id)
    property_id = await seed.create_property_row(
        organization_id=organization_id, landlord_id=landlord_id
    )
    renter_id = await seed.create_renter_row(organization_id=organization_id)
    return property_id, renter_id


async def _set_property_status(property_id, status: str) -> None:
    """Simula el efecto de `ContractService.activate` sobre la propiedad
    cuando el contrato `active` fue sembrado directo en DB (sin pasar por
    el endpoint) -- necesario para ejercitar `terminate` de forma aislada."""
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text("UPDATE properties SET status = :status WHERE id = :id"),
            {"id": str(property_id), "status": status},
        )


class TestCA0301CreateArsContractStaysDraft:
    """CA-03-01: Se crea un contrato ARS con % de mora, frecuencia de
    ajuste e indice de referencia; nace en `draft` y no genera periodos
    hasta activarse."""

    async def test_ca_03_01_create_ars_contract_with_adjustment_config_stays_draft(
        self, client, seed
    ):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])

        response = await client.post(
            "/v1/contracts",
            json={
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "currency": "ARS",
                "initial_amount": "150000.00",
                "start_date": "2026-01-01",
                "end_date": "2027-01-01",
                "daily_late_fee_pct": "0.5",
                "adjustment_frequency_months": 3,
                "adjustment_index": "icl",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["status"] == "draft"
        assert data["currency"] == "ARS"
        assert data["initial_amount"] == "150000.00"
        assert data["current_amount"] == "150000.00"
        assert data["adjustment_frequency_months"] == 3
        assert data["adjustment_index"] == "icl"

        assert await seed.get_property_status(property_id) == "available"

    async def test_create_contract_with_nonexistent_property_returns_404(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        _property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])

        response = await client.post(
            "/v1/contracts",
            json={
                "property_id": str(uuid.uuid4()),
                "renter_id": str(renter_id),
                "currency": "ARS",
                "initial_amount": "1000.00",
                "start_date": "2026-01-01",
                "end_date": "2027-01-01",
                "daily_late_fee_pct": "0.1",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
        assert response.json()["error"]["field"] == "property_id"

    async def test_create_contract_with_nonexistent_renter_returns_404(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, _renter_id = await _seed_property_and_renter(seed, owner["organization_id"])

        response = await client.post(
            "/v1/contracts",
            json={
                "property_id": str(property_id),
                "renter_id": str(uuid.uuid4()),
                "currency": "ARS",
                "initial_amount": "1000.00",
                "start_date": "2026-01-01",
                "end_date": "2027-01-01",
                "daily_late_fee_pct": "0.1",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
        assert response.json()["error"]["field"] == "renter_id"


class TestContractCreateValidations:
    """RF-02 §"Validaciones": end_date > start_date, duracion maxima
    razonable (<= 10 anios), y nota obligatoria si el indice es 'otro'."""

    async def test_end_date_not_after_start_date_returns_400(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])

        response = await client.post(
            "/v1/contracts",
            json={
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "currency": "ARS",
                "initial_amount": "1000.00",
                "start_date": "2026-06-01",
                "end_date": "2026-01-01",
                "daily_late_fee_pct": "0.1",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_contract_duration_over_10_years_returns_400(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])

        response = await client.post(
            "/v1/contracts",
            json={
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "currency": "ARS",
                "initial_amount": "1000.00",
                "start_date": "2026-01-01",
                "end_date": "2040-01-01",
                "daily_late_fee_pct": "0.1",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_adjustment_index_otro_without_notes_returns_400(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])

        response = await client.post(
            "/v1/contracts",
            json={
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "currency": "ARS",
                "initial_amount": "1000.00",
                "start_date": "2026-01-01",
                "end_date": "2027-01-01",
                "daily_late_fee_pct": "0.1",
                "adjustment_frequency_months": 6,
                "adjustment_index": "otro",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_adjustment_index_otro_with_notes_is_created_successfully(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])

        response = await client.post(
            "/v1/contracts",
            json={
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "currency": "ARS",
                "initial_amount": "1000.00",
                "start_date": "2026-01-01",
                "end_date": "2027-01-01",
                "daily_late_fee_pct": "0.1",
                "adjustment_frequency_months": 6,
                "adjustment_index": "otro",
                "adjustment_index_notes": "Indice pactado por contrato especifico",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 201
        assert response.json()["data"]["adjustment_index_notes"] == (
            "Indice pactado por contrato especifico"
        )


class TestCA0302ContractOverlap:
    """CA-03-02: Crear o activar un contrato cuya vigencia se superpone
    con otro `active` de la misma propiedad devuelve 409 CONTRACT_OVERLAP
    con el contrato en conflicto en `details`."""

    async def test_ca_03_02_create_overlapping_active_contract_returns_409_with_conflicting_id(
        self, client, seed
    ):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        active_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            start_date="2026-01-01",
            end_date="2026-12-31",
            status="active",
        )

        response = await client.post(
            "/v1/contracts",
            json={
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "currency": "ARS",
                "initial_amount": "1000.00",
                "start_date": "2026-06-01",
                "end_date": "2027-06-01",
                "daily_late_fee_pct": "0.1",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 409
        body = response.json()
        assert body["error"]["code"] == "CONTRACT_OVERLAP"
        assert body["error"]["details"]["conflicting_contract_id"] == str(active_id)

    async def test_ca_03_02_activate_draft_overlapping_active_contract_returns_409(
        self, client, seed
    ):
        """El solapamiento tambien se revalida al activar (no solo al
        crear) -- un `draft` que no chocaba en su ventana original puede
        chocar si otro contrato paso a `active` mientras tanto."""
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        draft_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            start_date="2026-06-01",
            end_date="2027-06-01",
            status="draft",
        )
        active_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            start_date="2026-01-01",
            end_date="2026-12-31",
            status="active",
        )

        response = await client.post(f"/v1/contracts/{draft_id}/activate", headers=owner["headers"])

        assert response.status_code == 409
        body = response.json()
        assert body["error"]["code"] == "CONTRACT_OVERLAP"
        assert body["error"]["details"]["conflicting_contract_id"] == str(active_id)

    async def test_activate_nondraft_contract_returns_422_invalid_status_transition(
        self, client, seed
    ):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        active_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            status="active",
        )

        response = await client.post(
            f"/v1/contracts/{active_id}/activate", headers=owner["headers"]
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_STATUS_TRANSITION"


class TestCA0303UsdContractRejectsAdjustment:
    """CA-03-03: Crear un contrato USD con frecuencia o indice de ajuste
    devuelve 400 VALIDATION_ERROR (RN-03)."""

    async def test_ca_03_03_usd_contract_with_adjustment_frequency_returns_400(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])

        response = await client.post(
            "/v1/contracts",
            json={
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "currency": "USD",
                "initial_amount": "500.00",
                "start_date": "2026-01-01",
                "end_date": "2027-01-01",
                "daily_late_fee_pct": "0.1",
                "adjustment_frequency_months": 6,
            },
            headers=owner["headers"],
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_ca_03_03_usd_contract_with_adjustment_index_returns_400(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])

        response = await client.post(
            "/v1/contracts",
            json={
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "currency": "USD",
                "initial_amount": "500.00",
                "start_date": "2026-01-01",
                "end_date": "2027-01-01",
                "daily_late_fee_pct": "0.1",
                "adjustment_index": "icl",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_usd_contract_without_adjustment_is_created_successfully(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])

        response = await client.post(
            "/v1/contracts",
            json={
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "currency": "USD",
                "initial_amount": "500.00",
                "start_date": "2026-01-01",
                "end_date": "2027-01-01",
                "daily_late_fee_pct": "0.1",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 201
        assert response.json()["data"]["currency"] == "USD"


class TestCA0306AmountNotEditableByPatch:
    """CA-03-06: El monto vigente de un contrato activo no puede editarse
    por PATCH (422 BUSINESS_RULE_VIOLATION -- RN-04); solo cambia via
    ajuste."""

    async def test_ca_03_06_patch_current_amount_on_active_contract_returns_422(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        contract_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            status="active",
        )

        response = await client.patch(
            f"/v1/contracts/{contract_id}",
            json={"current_amount": "999999.00"},
            headers=owner["headers"],
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "BUSINESS_RULE_VIOLATION"

    async def test_patch_current_amount_on_draft_contract_also_returns_422(self, client, seed):
        """RN-C04: el monto NUNCA se edita por PATCH, ni siquiera en
        `draft` -- sdd_03 §8 lo declara sin excepcion de estado."""
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        created = await client.post(
            "/v1/contracts",
            json={
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "currency": "ARS",
                "initial_amount": "1000.00",
                "start_date": "2026-01-01",
                "end_date": "2027-01-01",
                "daily_late_fee_pct": "0.1",
            },
            headers=owner["headers"],
        )
        contract_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/contracts/{contract_id}",
            json={"current_amount": "1.00"},
            headers=owner["headers"],
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "BUSINESS_RULE_VIOLATION"

    async def test_patch_notes_and_end_date_succeeds_and_is_audited(self, client, seed):
        """RF-03: "fechas de fin se pueden extender... quedando auditado"."""
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        created = await client.post(
            "/v1/contracts",
            json={
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "currency": "ARS",
                "initial_amount": "1000.00",
                "start_date": "2026-01-01",
                "end_date": "2027-01-01",
                "daily_late_fee_pct": "0.1",
            },
            headers=owner["headers"],
        )
        contract_id = created.json()["data"]["id"]

        response = await client.patch(
            f"/v1/contracts/{contract_id}",
            json={"notes": "Renovacion simple", "end_date": "2027-06-01"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["notes"] == "Renovacion simple"
        assert data["end_date"] == "2027-06-01"

        rows = await seed.audit_rows(owner["organization_id"], "contract.end_date_extended")
        assert any(str(r["entity_id"]) == contract_id for r in rows)


class TestCA0308AndCA0104TerminateReturnsPropertyToAvailable:
    """CA-03-08 + CA-01-04: al terminar un contrato, la propiedad vuelve
    a `available` y sus periodos impagos siguen visibles en el estado de
    deuda (no hay `rent_periods` todavia -- issue #20)."""

    async def test_ca_01_04_activate_sets_property_to_rented(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        created = await client.post(
            "/v1/contracts",
            json={
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "currency": "ARS",
                "initial_amount": "1000.00",
                "start_date": "2026-01-01",
                "end_date": "2027-01-01",
                "daily_late_fee_pct": "0.1",
            },
            headers=owner["headers"],
        )
        contract_id = created.json()["data"]["id"]

        response = await client.post(
            f"/v1/contracts/{contract_id}/activate", headers=owner["headers"]
        )

        assert response.status_code == 200
        assert response.json()["data"]["status"] == "active"
        assert await seed.get_property_status(property_id) == "rented"

    async def test_ca_03_08_ca_01_04_terminate_active_contract_returns_property_to_available(
        self, client, seed
    ):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        contract_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            status="active",
        )
        # `activate` no se ejercito via API en este test -- se simula su
        # efecto sobre la propiedad para aislar la asercion de `terminate`.
        await _set_property_status(property_id, "rented")

        response = await client.post(
            f"/v1/contracts/{contract_id}/terminate",
            json={"reason": "Mudanza anticipada del inquilino"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        assert response.json()["data"]["status"] == "terminated"
        assert await seed.get_property_status(property_id) == "available"

        rows = await seed.audit_rows(owner["organization_id"], "contract.terminated")
        matching = [r for r in rows if str(r["entity_id"]) == str(contract_id)]
        assert matching
        assert matching[0]["after_state"]["reason"] == "Mudanza anticipada del inquilino"

    async def test_terminate_non_active_contract_returns_422_contract_not_active(
        self, client, seed
    ):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        draft_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            status="draft",
        )

        response = await client.post(
            f"/v1/contracts/{draft_id}/terminate",
            json={"reason": "Intento invalido"},
            headers=owner["headers"],
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "CONTRACT_NOT_ACTIVE"


class TestContractListFilters:
    """RF-01: "Listado con filtros: estado, propiedad, inquilino, moneda,
    expiring_in_days"."""

    async def test_filter_by_status(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_id,
            renter_id=renter_id,
            status="active",
        )
        await client.post(
            "/v1/contracts",
            json={
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "currency": "ARS",
                "initial_amount": "1000.00",
                "start_date": "2030-01-01",
                "end_date": "2031-01-01",
                "daily_late_fee_pct": "0.1",
            },
            headers=owner["headers"],
        )

        response = await client.get(
            "/v1/contracts", params={"status": "draft"}, headers=owner["headers"]
        )

        assert response.status_code == 200
        statuses = {item["status"] for item in response.json()["data"]}
        assert statuses == {"draft"}

    async def test_filter_by_currency(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        await client.post(
            "/v1/contracts",
            json={
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "currency": "USD",
                "initial_amount": "500.00",
                "start_date": "2026-01-01",
                "end_date": "2027-01-01",
                "daily_late_fee_pct": "0.1",
            },
            headers=owner["headers"],
        )

        response = await client.get(
            "/v1/contracts", params={"currency": "USD"}, headers=owner["headers"]
        )

        assert response.status_code == 200
        currencies = {item["currency"] for item in response.json()["data"]}
        assert currencies == {"USD"}

    async def test_get_existing_contract_returns_200_with_data(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        created = await client.post(
            "/v1/contracts",
            json={
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "currency": "ARS",
                "initial_amount": "1000.00",
                "start_date": "2026-01-01",
                "end_date": "2027-01-01",
                "daily_late_fee_pct": "0.1",
            },
            headers=owner["headers"],
        )
        contract_id = created.json()["data"]["id"]

        response = await client.get(f"/v1/contracts/{contract_id}", headers=owner["headers"])

        assert response.status_code == 200
        assert response.json()["data"]["id"] == contract_id

    async def test_get_nonexistent_contract_returns_404(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.get(f"/v1/contracts/{uuid.uuid4()}", headers=owner["headers"])

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_filter_by_property_id(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_a, renter_a = await _seed_property_and_renter(seed, owner["organization_id"])
        property_b, renter_b = await _seed_property_and_renter(seed, owner["organization_id"])
        contract_a_id = await seed.create_contract_row(
            organization_id=owner["organization_id"], property_id=property_a, renter_id=renter_a
        )
        await seed.create_contract_row(
            organization_id=owner["organization_id"], property_id=property_b, renter_id=renter_b
        )

        response = await client.get(
            "/v1/contracts", params={"property_id": str(property_a)}, headers=owner["headers"]
        )

        assert response.status_code == 200
        ids = {item["id"] for item in response.json()["data"]}
        assert ids == {str(contract_a_id)}

    async def test_filter_by_renter_id(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_a, renter_a = await _seed_property_and_renter(seed, owner["organization_id"])
        property_b, renter_b = await _seed_property_and_renter(seed, owner["organization_id"])
        contract_a_id = await seed.create_contract_row(
            organization_id=owner["organization_id"], property_id=property_a, renter_id=renter_a
        )
        await seed.create_contract_row(
            organization_id=owner["organization_id"], property_id=property_b, renter_id=renter_b
        )

        response = await client.get(
            "/v1/contracts", params={"renter_id": str(renter_a)}, headers=owner["headers"]
        )

        assert response.status_code == 200
        ids = {item["id"] for item in response.json()["data"]}
        assert ids == {str(contract_a_id)}

    async def test_filter_by_expiring_in_days_only_returns_active_within_threshold(
        self, client, seed
    ):
        """RF-01/RF-05: "vence dentro de N dias" -- solo contratos
        `active` cuyo `end_date` cae dentro de la ventana."""
        _org, owner = await _seed_org_with_owner(seed)
        property_soon, renter_soon = await _seed_property_and_renter(seed, owner["organization_id"])
        property_far, renter_far = await _seed_property_and_renter(seed, owner["organization_id"])
        property_draft, renter_draft = await _seed_property_and_renter(
            seed, owner["organization_id"]
        )
        expiring_soon_id = await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_soon,
            renter_id=renter_soon,
            start_date="2025-01-01",
            end_date="2026-08-20",
            status="active",
        )
        await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_far,
            renter_id=renter_far,
            start_date="2025-01-01",
            end_date="2030-01-01",
            status="active",
        )
        await seed.create_contract_row(
            organization_id=owner["organization_id"],
            property_id=property_draft,
            renter_id=renter_draft,
            start_date="2026-01-01",
            end_date="2026-08-25",
            status="draft",
        )

        response = await client.get(
            "/v1/contracts", params={"expiring_in_days": 30}, headers=owner["headers"]
        )

        assert response.status_code == 200
        ids = {item["id"] for item in response.json()["data"]}
        assert ids == {str(expiring_soon_id)}

    async def test_list_contracts_paginates_with_cursor(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])
        for i in range(3):
            await client.post(
                "/v1/contracts",
                json={
                    "property_id": str(property_id),
                    "renter_id": str(renter_id),
                    "currency": "USD",
                    "initial_amount": "500.00",
                    "start_date": f"{2030 + i}-01-01",
                    "end_date": f"{2031 + i}-01-01",
                    "daily_late_fee_pct": "0.1",
                },
                headers=owner["headers"],
            )

        first_page = await client.get(
            "/v1/contracts", params={"limit": 2}, headers=owner["headers"]
        )
        assert len(first_page.json()["data"]) == 2
        next_cursor = first_page.json()["meta"]["next_cursor"]
        assert next_cursor is not None

        second_page = await client.get(
            "/v1/contracts",
            params={"limit": 2, "cursor": next_cursor},
            headers=owner["headers"],
        )
        assert len(second_page.json()["data"]) == 1
        first_ids = {item["id"] for item in first_page.json()["data"]}
        second_ids = {item["id"] for item in second_page.json()["data"]}
        assert first_ids.isdisjoint(second_ids)
