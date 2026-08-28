"""tests/integration/contracts/test_adjustments.py

SDD: docs/sdd/features/spec_module_03_contratos.md RF-04 +
core/sdd_03_api_contracts.md §8 "Contratos".
Implements: CA-03-04 (bandeja/deteccion, cubierta a nivel HTTP para la
            parte "aparece en la bandeja") y CA-03-05 (aplicacion manual).
            Tambien cubre RN-C06 v2 (issue #107): deteccion futura
            anclada en el ultimo ajuste sintetico de una cadena
            `historical_amounts[]`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from adminprop.db.session import get_session_factory
from adminprop.modules.payments.repository import RentPeriodRepository

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
    maintenance = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["maintenance"],
        role_name="maintenance",
    )
    return org, owner, admin, maintenance


async def _seed_property_and_renter(seed, organization_id):
    landlord_id = await seed.create_landlord_row(organization_id=organization_id)
    property_id = await seed.create_property_row(
        organization_id=organization_id, landlord_id=landlord_id
    )
    renter_id = await seed.create_renter_row(organization_id=organization_id)
    return property_id, renter_id


async def _seed_active_ars_contract(seed, organization_id, *, current_amount="100000.00"):
    property_id, renter_id = await _seed_property_and_renter(seed, organization_id)
    contract_id = await seed.create_contract_row(
        organization_id=organization_id,
        property_id=property_id,
        renter_id=renter_id,
        currency="ARS",
        initial_amount=current_amount,
        status="active",
    )
    return contract_id


class TestCA0304PendingAdjustmentAppearsInInboxAndHistory:
    """CA-03-04: el contrato con ajuste pendiente aparece en la bandeja
    (`GET /adjustments?status=pending`) y en el historial del contrato
    (`GET /contracts/:id/adjustments`)."""

    async def test_ca_03_04_pending_adjustment_appears_in_inbox_and_contract_history(
        self, client, seed
    ):
        _org, owner, _admin, _maintenance = await _seed_org_with_owner_and_admin(seed)
        contract_id = await _seed_active_ars_contract(seed, owner["organization_id"])
        adjustment_id = await seed.create_adjustment_row(
            organization_id=owner["organization_id"],
            contract_id=contract_id,
            status="pending",
        )

        inbox_response = await client.get(
            "/v1/adjustments", params={"status": "pending"}, headers=owner["headers"]
        )
        assert inbox_response.status_code == 200
        inbox_ids = {item["id"] for item in inbox_response.json()["data"]}
        assert str(adjustment_id) in inbox_ids

        history_response = await client.get(
            f"/v1/contracts/{contract_id}/adjustments", headers=owner["headers"]
        )
        assert history_response.status_code == 200
        history = history_response.json()["data"]
        assert len(history) == 1
        assert history[0]["id"] == str(adjustment_id)
        assert history[0]["status"] == "pending"
        assert history[0]["pct_applied"] is None
        assert history[0]["new_amount"] is None

    async def test_applied_adjustment_does_not_appear_in_pending_inbox(self, client, seed):
        _org, owner, _admin, _maintenance = await _seed_org_with_owner_and_admin(seed)
        contract_id = await _seed_active_ars_contract(seed, owner["organization_id"])
        await seed.create_adjustment_row(
            organization_id=owner["organization_id"],
            contract_id=contract_id,
            status="applied",
            pct_applied="10.00",
            new_amount="110000.00",
            applied_by=owner["id"],
        )

        response = await client.get(
            "/v1/adjustments", params={"status": "pending"}, headers=owner["headers"]
        )

        assert response.status_code == 200
        assert response.json()["data"] == []

    async def test_list_contract_adjustments_for_nonexistent_contract_returns_404(
        self, client, seed
    ):
        _org, owner, _admin, _maintenance = await _seed_org_with_owner_and_admin(seed)

        response = await client.get(
            f"/v1/contracts/{uuid.uuid4()}/adjustments", headers=owner["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_maintenance_role_cannot_read_adjustments_inbox(self, client, seed):
        """RN-A01: `maintenance` no tiene ningun permiso `contract:*`."""
        _org, _owner, _admin, maintenance = await _seed_org_with_owner_and_admin(seed)

        response = await client.get(
            "/v1/adjustments", params={"status": "pending"}, headers=maintenance["headers"]
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    async def test_status_filter_other_than_pending_returns_empty_list(self, client, seed):
        """sdd_03 §8: el unico valor soportado es `pending` -- otros
        valores no exponen un filtro que el SDD no especifica."""
        _org, owner, _admin, _maintenance = await _seed_org_with_owner_and_admin(seed)
        contract_id = await _seed_active_ars_contract(seed, owner["organization_id"])
        await seed.create_adjustment_row(
            organization_id=owner["organization_id"],
            contract_id=contract_id,
            status="pending",
        )

        response = await client.get(
            "/v1/adjustments", params={"status": "applied"}, headers=owner["headers"]
        )

        assert response.status_code == 200
        assert response.json()["data"] == []

    async def test_pending_inbox_paginates_with_cursor(self, client, seed):
        """CA-16-03: solo un `pending` por contrato -- la paginacion se
        ejercita con 3 contratos distintos, cada uno con su propio ajuste
        `pending`."""
        _org, owner, _admin, _maintenance = await _seed_org_with_owner_and_admin(seed)
        for _ in range(3):
            contract_id = await _seed_active_ars_contract(seed, owner["organization_id"])
            await seed.create_adjustment_row(
                organization_id=owner["organization_id"],
                contract_id=contract_id,
                status="pending",
            )

        first_page = await client.get(
            "/v1/adjustments",
            params={"status": "pending", "limit": 2},
            headers=owner["headers"],
        )
        assert first_page.status_code == 200
        assert len(first_page.json()["data"]) == 2
        next_cursor = first_page.json()["meta"]["next_cursor"]
        assert next_cursor is not None

        second_page = await client.get(
            "/v1/adjustments",
            params={"status": "pending", "limit": 2, "cursor": next_cursor},
            headers=owner["headers"],
        )
        assert second_page.status_code == 200
        assert len(second_page.json()["data"]) == 1
        first_ids = {item["id"] for item in first_page.json()["data"]}
        second_ids = {item["id"] for item in second_page.json()["data"]}
        assert first_ids.isdisjoint(second_ids)


class TestCA0305ApplyAdjustment:
    """CA-03-05: al aplicar el ajuste con un %, el monto vigente se
    actualiza, el historial registra pct/monto anterior/monto nuevo/autor,
    y el ajuste queda `applied`."""

    async def test_ca_03_05_apply_updates_contract_amount_and_history(self, client, seed):
        _org, owner, _admin, _maintenance = await _seed_org_with_owner_and_admin(seed)
        contract_id = await _seed_active_ars_contract(
            seed, owner["organization_id"], current_amount="100000.00"
        )
        adjustment_id = await seed.create_adjustment_row(
            organization_id=owner["organization_id"],
            contract_id=contract_id,
            status="pending",
            previous_amount="100000.00",
        )

        response = await client.post(
            f"/v1/adjustments/{adjustment_id}/apply",
            json={"pct": "10.00"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "applied"
        assert data["pct_applied"] == "10.0000"
        assert data["previous_amount"] == "100000.00"
        assert data["new_amount"] == "110000.00"
        assert data["applied_by"] == str(owner["id"])
        assert data["applied_at"] is not None

        assert await seed.get_contract_current_amount(contract_id) == "110000.00"

        rows = await seed.audit_rows(owner["organization_id"], "adjustment.applied")
        matching = [r for r in rows if str(r["entity_id"]) == str(adjustment_id)]
        assert matching
        assert matching[0]["after_state"]["pct_applied"] == "10.00"
        assert matching[0]["after_state"]["new_amount"] == "110000.00"
        assert matching[0]["before_state"]["previous_amount"] == "100000.00"

    async def test_ca_04_02_apply_generates_rent_period_with_new_amount(self, client, seed):
        """CA-04-02: "un contrato con ajuste pendiente no genera el
        período del mes hasta aplicar el %; al aplicarlo, el período nace
        con el monto nuevo" (spec_module_04_cobranzas.md §RF-01, RN-P01)."""
        _org, owner, _admin, _maintenance = await _seed_org_with_owner_and_admin(seed)
        contract_id = await _seed_active_ars_contract(
            seed, owner["organization_id"], current_amount="100000.00"
        )
        due_period = "2026-04-01"
        adjustment_id = await seed.create_adjustment_row(
            organization_id=owner["organization_id"],
            contract_id=contract_id,
            due_period=due_period,
            status="pending",
            previous_amount="100000.00",
        )

        # Antes de aplicar: el rent_period de ese mes no existe (RN-P01).
        # issue #42: verificacion cruda fuera del flujo HTTP -- sin tenant
        # context seteado, RLS devolveria 0 filas siempre (falso negativo).
        # Bypass explicito (no es lo que se esta probando aca).
        import sqlalchemy as sa

        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
            before = await RentPeriodRepository(session).get_by_contract_and_period(
                contract_id, owner["organization_id"], date.fromisoformat(due_period)
            )
        assert before is None

        response = await client.post(
            f"/v1/adjustments/{adjustment_id}/apply",
            json={"pct": "10.00"},
            headers=owner["headers"],
        )
        assert response.status_code == 200

        async with session_factory() as session, session.begin():
            await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
            after = await RentPeriodRepository(session).get_by_contract_and_period(
                contract_id, owner["organization_id"], date.fromisoformat(due_period)
            )
        assert after is not None
        assert str(after.amount_due) == "110000.00"
        assert after.currency == "ARS"
        assert after.status == "pending"

    async def test_apply_accepts_negative_pct_within_sanity_cap(self, client, seed):
        """RF-02 §"Validaciones": pct puede ser negativo (deflacion)."""
        _org, owner, _admin, _maintenance = await _seed_org_with_owner_and_admin(seed)
        contract_id = await _seed_active_ars_contract(
            seed, owner["organization_id"], current_amount="100000.00"
        )
        adjustment_id = await seed.create_adjustment_row(
            organization_id=owner["organization_id"],
            contract_id=contract_id,
            status="pending",
            previous_amount="100000.00",
        )

        response = await client.post(
            f"/v1/adjustments/{adjustment_id}/apply",
            json={"pct": "-10.00"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["new_amount"] == "90000.00"

    async def test_apply_pct_over_sanity_cap_returns_400_validation_error(self, client, seed):
        """RF-02 §"Validaciones": tope de sanidad ±500%."""
        _org, owner, _admin, _maintenance = await _seed_org_with_owner_and_admin(seed)
        contract_id = await _seed_active_ars_contract(seed, owner["organization_id"])
        adjustment_id = await seed.create_adjustment_row(
            organization_id=owner["organization_id"],
            contract_id=contract_id,
            status="pending",
        )

        response = await client.post(
            f"/v1/adjustments/{adjustment_id}/apply",
            json={"pct": "501"},
            headers=owner["headers"],
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_apply_without_pct_returns_400_adjustment_pct_required(self, client, seed):
        _org, owner, _admin, _maintenance = await _seed_org_with_owner_and_admin(seed)
        contract_id = await _seed_active_ars_contract(seed, owner["organization_id"])
        adjustment_id = await seed.create_adjustment_row(
            organization_id=owner["organization_id"],
            contract_id=contract_id,
            status="pending",
        )

        response = await client.post(
            f"/v1/adjustments/{adjustment_id}/apply", json={}, headers=owner["headers"]
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "ADJUSTMENT_PCT_REQUIRED"

    async def test_apply_already_applied_adjustment_returns_409_immutable(self, client, seed):
        """sdd_02 §2.8: un ajuste `applied` es inmutable."""
        _org, owner, _admin, _maintenance = await _seed_org_with_owner_and_admin(seed)
        contract_id = await _seed_active_ars_contract(seed, owner["organization_id"])
        adjustment_id = await seed.create_adjustment_row(
            organization_id=owner["organization_id"],
            contract_id=contract_id,
            status="applied",
            pct_applied="5.00",
            new_amount="105000.00",
            applied_by=owner["id"],
        )

        response = await client.post(
            f"/v1/adjustments/{adjustment_id}/apply",
            json={"pct": "5.00"},
            headers=owner["headers"],
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "ADJUSTMENT_ALREADY_APPLIED"

    async def test_apply_nonexistent_adjustment_returns_404(self, client, seed):
        _org, owner, _admin, _maintenance = await _seed_org_with_owner_and_admin(seed)

        response = await client.post(
            f"/v1/adjustments/{uuid.uuid4()}/apply",
            json={"pct": "5.00"},
            headers=owner["headers"],
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_admin_can_apply_adjustment(self, client, seed):
        """sdd_03 §"Resumen de Autorizacion por Recurso": admin tiene
        acceso total a contratos, igual que owner."""
        _org, _owner, admin, _maintenance = await _seed_org_with_owner_and_admin(seed)
        contract_id = await _seed_active_ars_contract(seed, admin["organization_id"])
        adjustment_id = await seed.create_adjustment_row(
            organization_id=admin["organization_id"],
            contract_id=contract_id,
            status="pending",
        )

        response = await client.post(
            f"/v1/adjustments/{adjustment_id}/apply",
            json={"pct": "2.50"},
            headers=admin["headers"],
        )

        assert response.status_code == 200

    async def test_maintenance_role_cannot_apply_adjustment(self, client, seed):
        """RN-A01: `maintenance` nunca tiene `adjustment:apply`."""
        _org, owner, _admin, maintenance = await _seed_org_with_owner_and_admin(seed)
        contract_id = await _seed_active_ars_contract(seed, owner["organization_id"])
        adjustment_id = await seed.create_adjustment_row(
            organization_id=owner["organization_id"],
            contract_id=contract_id,
            status="pending",
        )

        response = await client.post(
            f"/v1/adjustments/{adjustment_id}/apply",
            json={"pct": "5.00"},
            headers=maintenance["headers"],
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"


class TestNextAdjustmentAnchoredOnLastSyntheticOfHistoricalAmountsChain:
    """RN-C06 v2 (issue #107, supersede parcialmente CA-03-10/issue #100
    -- `detect_due_adjustments` sigue anclando SIEMPRE en el `due_period`
    del ultimo ajuste `applied`, RN-C03): el proximo ajuste por indice de
    un contrato ARS dado de alta en curso con `historical_amounts[]` se
    detecta contando desde el `due_period` del ULTIMO ajuste sintetico de
    la cadena (el mas reciente), no desde `start_date` -- sin tocar la
    logica de `detect_due_adjustments`."""

    async def test_next_due_period_counted_from_last_synthetic_in_chain(self, client, seed):
        import sqlalchemy as sa

        from adminprop.modules.contracts.adjustment_repository import ContractAdjustmentRepository
        from adminprop.modules.contracts.adjustment_service import (
            ContractAdjustmentService,
            _add_months,
        )
        from adminprop.modules.contracts.repository import ContractRepository

        _org, owner, _admin, _maintenance = await _seed_org_with_owner_and_admin(seed)
        property_id, renter_id = await _seed_property_and_renter(seed, owner["organization_id"])

        today = datetime.now(UTC).date()
        current_month = date(today.year, today.month, 1)
        # RN-C06 v2: `start_date` 9 meses atras, freq=3 -> 3 tramos
        # transcurridos mas alla del inicial (4 elementos totales); el
        # ULTIMO sintetico de la cadena SIEMPRE cae dentro del tramo que
        # contiene "hoy" (por construccion, `expected_tramo_count`) --
        # por eso el proximo `due_period` es necesariamente futuro
        # respecto de "hoy real"; se simula el paso del tiempo pasandole
        # a `detect_due_adjustments` un `today` posterior al
        # `expected_due_period`, en vez de depender del reloj real (el
        # worker `_detect_due_adjustments_async` siempre usa
        # `datetime.now()`, no serviria para este escenario).
        start_date = _add_months(current_month, -9)
        expected_due_period = _add_months(current_month, 3)

        created = await client.post(
            "/v1/contracts",
            json={
                "property_id": str(property_id),
                "renter_id": str(renter_id),
                "currency": "ARS",
                "initial_amount": "100000.00",
                "start_date": start_date.isoformat(),
                "end_date": _add_months(current_month, 24).isoformat(),
                "daily_late_fee_pct": "0.1",
                "adjustment_frequency_months": 3,
                "adjustment_index": "icl",
                "historical_amounts": ["100000.00", "120000.00", "140000.00", "150000.00"],
            },
            headers=owner["headers"],
        )
        assert created.status_code == 201
        contract_id = created.json()["data"]["id"]

        activated = await client.post(
            f"/v1/contracts/{contract_id}/activate", headers=owner["headers"]
        )
        assert activated.status_code == 200

        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
            service = ContractAdjustmentService(
                ContractAdjustmentRepository(session), ContractRepository(session)
            )
            await service.detect_due_adjustments(
                organization_id=owner["organization_id"],
                today=expected_due_period + timedelta(days=1),
            )

        async with session_factory() as session:
            await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
            result = await session.execute(
                sa.text(
                    "SELECT due_period, status FROM contract_adjustments "
                    "WHERE contract_id = :contract_id AND status = 'pending'"
                ),
                {"contract_id": contract_id},
            )
            rows = [dict(row._mapping) for row in result]

        assert len(rows) == 1
        assert rows[0]["due_period"] == expected_due_period
