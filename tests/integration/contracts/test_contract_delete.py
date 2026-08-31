"""tests/integration/contracts/test_contract_delete.py

SDD: docs/sdd/features/spec_module_03_contratos.md v1.7 RF-07 (RN-13 =
RN-C08 de sdd_02 v1.9) + core/sdd_03_api_contracts.md v1.17 §8
(issue #124, decision #130).
Implements: CA-03-36, CA-03-37, CA-03-38, CA-03-39, CA-03-40.
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


async def _seed_contract_fixture(seed, organization_id, *, status: str = "active"):
    """Contrato sembrado directo en DB (sin pasar por el API) con su
    propiedad/inquilino -- devuelve los tres IDs."""
    landlord_id = await seed.create_landlord_row(organization_id=organization_id)
    property_id = await seed.create_property_row(
        organization_id=organization_id,
        landlord_id=landlord_id,
        status="rented" if status == "active" else "available",
    )
    renter_id = await seed.create_renter_row(organization_id=organization_id)
    contract_id = await seed.create_contract_row(
        organization_id=organization_id,
        property_id=property_id,
        renter_id=renter_id,
        status=status,
    )
    return contract_id, property_id, renter_id


async def _contract_row(contract_id) -> dict | None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        result = await session.execute(
            sa.text("SELECT id, status, deleted_at FROM contracts WHERE id = :id"),
            {"id": str(contract_id)},
        )
        row = result.first()
        return dict(row._mapping) if row is not None else None


class TestCA0336ContractDeletePermission:
    """CA-03-36: `DELETE /contracts/:id` con un usuario sin
    `contract:delete` (ej. `admin` con `contract:manage`) devuelve
    `403 FORBIDDEN`; el rol `owner` lo tiene sembrado (organizaciones
    nuevas por provisioning, existentes por migracion de backfill)."""

    async def test_ca_03_36_admin_without_contract_delete_gets_403(self, client, seed):
        org, owner = await _seed_org_with_owner(seed)
        admin = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["admin"],
            role_name="admin",
        )
        contract_id, _property_id, _renter_id = await _seed_contract_fixture(
            seed, org["organization_id"]
        )

        response = await client.delete(f"/v1/contracts/{contract_id}", headers=admin["headers"])

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"
        # El contrato sigue intacto y visible para el owner.
        detail = await client.get(f"/v1/contracts/{contract_id}", headers=owner["headers"])
        assert detail.status_code == 200

    async def test_ca_03_36_check_is_by_atomic_permission_not_role_name(self, client, seed):
        """CLAUDE.md §6 / decision #6: el chequeo es por permiso atomico,
        nunca por nombre de rol -- un rol CUSTOM que porta
        `contract:delete` puede eliminar aunque no se llame `owner`
        (mismo patron que CA-R124-03 de `contract:terminate`)."""
        org, _owner = await _seed_org_with_owner(seed)
        custom_role_id = await seed.create_role(
            org["organization_id"], name="custom", permissions=["contract:delete"]
        )
        custom = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=custom_role_id,
            role_name="custom",
            permissions=["contract:delete"],
        )
        contract_id, _property_id, _renter_id = await _seed_contract_fixture(
            seed, org["organization_id"], status="draft"
        )

        response = await client.delete(f"/v1/contracts/{contract_id}", headers=custom["headers"])

        assert response.status_code == 204


class TestCA0337LogicalDeleteAnyStatus:
    """CA-03-37: el `owner` elimina un contrato en cualquier estado
    (incluso `active`) -> 204; el borrado es logico (`deleted_at` en DB,
    la fila persiste), queda auditado (`contract.deleted` con el estado
    previo), el contrato desaparece de `GET /contracts` y su
    `GET /contracts/:id` devuelve 404."""

    @pytest.mark.parametrize("contract_status", ["draft", "active", "terminated"])
    async def test_ca_03_37_owner_deletes_contract_in_any_status(
        self, client, seed, contract_status
    ):
        org, owner = await _seed_org_with_owner(seed)
        contract_id, _property_id, _renter_id = await _seed_contract_fixture(
            seed, org["organization_id"], status=contract_status
        )

        response = await client.delete(f"/v1/contracts/{contract_id}", headers=owner["headers"])

        assert response.status_code == 204
        # RN-D02: la fila persiste con `deleted_at` seteado (nunca DELETE
        # fisico) y conserva su `status` previo.
        row = await _contract_row(contract_id)
        assert row is not None
        assert row["deleted_at"] is not None
        assert row["status"] == contract_status

    async def test_ca_03_37_deleted_contract_disappears_from_listing_and_detail(self, client, seed):
        org, owner = await _seed_org_with_owner(seed)
        contract_id, _property_id, _renter_id = await _seed_contract_fixture(
            seed, org["organization_id"]
        )

        await client.delete(f"/v1/contracts/{contract_id}", headers=owner["headers"])

        listing = await client.get("/v1/contracts", headers=owner["headers"])
        assert str(contract_id) not in {item["id"] for item in listing.json()["data"]}

        detail = await client.get(f"/v1/contracts/{contract_id}", headers=owner["headers"])
        assert detail.status_code == 404
        assert detail.json()["error"]["code"] == "NOT_FOUND"

        # Sub-endpoints tambien 404 (RF-07): PATCH y terminate.
        patched = await client.patch(
            f"/v1/contracts/{contract_id}", json={"notes": "x"}, headers=owner["headers"]
        )
        assert patched.status_code == 404
        terminated = await client.post(
            f"/v1/contracts/{contract_id}/terminate",
            json={"reason": "ya eliminado"},
            headers=owner["headers"],
        )
        assert terminated.status_code == 404

    async def test_ca_03_37_delete_is_audited_with_previous_status_and_actor(self, client, seed):
        org, owner = await _seed_org_with_owner(seed)
        contract_id, _property_id, _renter_id = await _seed_contract_fixture(
            seed, org["organization_id"]
        )

        await client.delete(f"/v1/contracts/{contract_id}", headers=owner["headers"])

        rows = await seed.audit_rows(org["organization_id"], "contract.deleted")
        assert len(rows) == 1
        assert str(rows[0]["entity_id"]) == str(contract_id)
        assert str(rows[0]["user_id"]) == str(owner["id"])
        assert rows[0]["before_state"] == {"status": "active"}

    async def test_ca_03_37_second_delete_returns_404(self, client, seed):
        org, owner = await _seed_org_with_owner(seed)
        contract_id, _property_id, _renter_id = await _seed_contract_fixture(
            seed, org["organization_id"], status="draft"
        )
        await client.delete(f"/v1/contracts/{contract_id}", headers=owner["headers"])

        second = await client.delete(f"/v1/contracts/{contract_id}", headers=owner["headers"])

        assert second.status_code == 404
        assert second.json()["error"]["code"] == "NOT_FOUND"

    async def test_delete_contract_of_another_organization_returns_404(self, client, seed):
        """RN-D01: cross-tenant es 404, nunca 403 -- mismo criterio que el
        resto de `test_tenant_isolation.py`."""
        _org_a, owner_a = await _seed_org_with_owner(seed)
        org_b = await seed.create_organization_with_system_roles()
        contract_b, _property_id, _renter_id = await _seed_contract_fixture(
            seed, org_b["organization_id"]
        )

        response = await client.delete(f"/v1/contracts/{contract_b}", headers=owner_a["headers"])

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
        assert (await _contract_row(contract_b))["deleted_at"] is None


class TestCA0338ActiveContractDeletionStopsFuturePeriods:
    """CA-03-38: al eliminar un contrato `active`, su propiedad vuelve a
    `available` y el job mensual `generate_rent_periods` NO genera el
    periodo siguiente del contrato eliminado (los demas contratos activos
    no se ven afectados)."""

    async def test_ca_03_38_property_returns_to_available(self, client, seed):
        org, owner = await _seed_org_with_owner(seed)
        contract_id, property_id, _renter_id = await _seed_contract_fixture(
            seed, org["organization_id"]
        )
        assert await seed.get_property_status(property_id) == "rented"

        response = await client.delete(f"/v1/contracts/{contract_id}", headers=owner["headers"])

        assert response.status_code == 204
        assert await seed.get_property_status(property_id) == "available"

    async def test_ca_03_38_monthly_job_skips_deleted_contract(self, client, seed):
        from adminprop.workers.notification_worker import _generate_rent_periods_async

        org, owner = await _seed_org_with_owner(seed)
        deleted_contract, _property_a, _renter_a = await _seed_contract_fixture(
            seed, org["organization_id"]
        )
        surviving_contract, _property_b, _renter_b = await _seed_contract_fixture(
            seed, org["organization_id"]
        )
        await client.delete(f"/v1/contracts/{deleted_contract}", headers=owner["headers"])

        await _generate_rent_periods_async(f"test-{uuid.uuid4().hex[:8]}")

        session_factory = get_session_factory()
        async with session_factory() as session:
            await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
            result = await session.execute(
                sa.text(
                    "SELECT contract_id, COUNT(*) FROM rent_periods "
                    "WHERE contract_id IN (:deleted, :surviving) GROUP BY contract_id"
                ),
                {"deleted": str(deleted_contract), "surviving": str(surviving_contract)},
            )
            counts = {str(row[0]): row[1] for row in result}

        # RN-C08: el eliminado no genera NINGUN periodo; el activo que
        # sobrevive genera el del mes en curso (control positivo).
        assert str(deleted_contract) not in counts
        assert counts.get(str(surviving_contract)) == 1


class TestCA0339DebtStopsComputing:
    """CA-03-39: tras eliminar un contrato con periodos impagos, su deuda
    deja de computarse -- sus `rent_periods` desaparecen de
    `GET /rent-periods` y de `GET /debt`, el detalle del periodo devuelve
    404 y `POST /rent-periods/:id/payments` sobre el devuelve 404; un
    ajuste `pending` suyo desaparece de `GET /adjustments?status=pending`."""

    async def test_ca_03_39_periods_leave_panel_debt_and_reject_new_payments(self, client, seed):
        org, owner = await _seed_org_with_owner(seed)
        contract_id, _property_id, _renter_id = await _seed_contract_fixture(
            seed, org["organization_id"]
        )
        rent_period_id = await seed.create_rent_period_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            period="2026-06-01",
            status="pending",
        )

        # Sanidad pre-borrado: el periodo computa en panel y deuda.
        panel_before = await client.get(
            "/v1/rent-periods", params={"period": "2026-06"}, headers=owner["headers"]
        )
        assert str(rent_period_id) in {item["id"] for item in panel_before.json()["data"]}
        debt_before = await client.get("/v1/debt", headers=owner["headers"])
        assert str(contract_id) in {e["contract_id"] for e in debt_before.json()["data"]}

        await client.delete(f"/v1/contracts/{contract_id}", headers=owner["headers"])

        panel = await client.get(
            "/v1/rent-periods", params={"period": "2026-06"}, headers=owner["headers"]
        )
        assert str(rent_period_id) not in {item["id"] for item in panel.json()["data"]}

        debt = await client.get("/v1/debt", headers=owner["headers"])
        assert str(contract_id) not in {e["contract_id"] for e in debt.json()["data"]}

        detail = await client.get(f"/v1/rent-periods/{rent_period_id}", headers=owner["headers"])
        assert detail.status_code == 404

        payment = await client.post(
            f"/v1/rent-periods/{rent_period_id}/payments",
            json={
                "payment_date": "2026-06-05",
                "method": "cash",
                "payment_currency": "ARS",
                "amount": "100000.00",
                "destination": "agency_account",
                "charged_interest": "0.00",
            },
            headers=owner["headers"],
        )
        assert payment.status_code == 404

    async def test_ca_03_39_pending_adjustment_leaves_inbox(self, client, seed):
        org, owner = await _seed_org_with_owner(seed)
        contract_id, _property_id, _renter_id = await _seed_contract_fixture(
            seed, org["organization_id"]
        )
        adjustment_id = await seed.create_adjustment_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            due_period="2026-09-01",
            status="pending",
        )
        inbox_before = await client.get(
            "/v1/adjustments", params={"status": "pending"}, headers=owner["headers"]
        )
        assert str(adjustment_id) in {item["id"] for item in inbox_before.json()["data"]}

        await client.delete(f"/v1/contracts/{contract_id}", headers=owner["headers"])

        inbox = await client.get(
            "/v1/adjustments", params={"status": "pending"}, headers=owner["headers"]
        )
        assert str(adjustment_id) not in {item["id"] for item in inbox.json()["data"]}

        # Aplicarlo igualmente es 404 (el contrato ya no se resuelve).
        applied = await client.post(
            f"/v1/adjustments/{adjustment_id}/apply",
            json={"pct": "10.0"},
            headers=owner["headers"],
        )
        assert applied.status_code == 404


class TestCA0340IssuedPaymentsStayIntact:
    """CA-03-40 (parte cobros): un cobro ya registrado del contrato
    eliminado queda intacto (no se anula ni se borra) y su recibo sigue
    descargable -- la lectura historica no filtra `deleted_at` (RN-12).
    La parte liquidaciones vive en
    tests/integration/settlements/test_settlement_after_contract_delete.py."""

    async def test_ca_03_40_existing_payment_and_receipt_survive_deletion(self, client, seed):
        org, owner = await _seed_org_with_owner(seed)
        contract_id, _property_id, _renter_id = await _seed_contract_fixture(
            seed, org["organization_id"]
        )
        rent_period_id = await seed.create_rent_period_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            period="2026-06-01",
            status="pending",
        )
        created = await client.post(
            f"/v1/rent-periods/{rent_period_id}/payments",
            json={
                "payment_date": "2026-06-05",
                "method": "transfer",
                "payment_currency": "ARS",
                "amount": "100000.00",
                "destination": "agency_account",
                "charged_interest": "0.00",
            },
            headers=owner["headers"],
        )
        assert created.status_code == 201
        payment_id = created.json()["data"]["id"]

        await client.delete(f"/v1/contracts/{contract_id}", headers=owner["headers"])

        # El cobro sigue integro en DB (ni anulado ni borrado).
        session_factory = get_session_factory()
        async with session_factory() as session:
            await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
            result = await session.execute(
                sa.text("SELECT amount, voided_at FROM payments WHERE id = :id"),
                {"id": str(payment_id)},
            )
            row = result.first()
        assert row is not None
        assert str(row[0]) == "100000.00"
        assert row[1] is None

        # El recibo del cobro existente sigue descargable (RF-07 de
        # cobranzas -- el join de lectura historica no filtra deleted_at).
        receipt = await client.get(f"/v1/payments/{payment_id}/receipt", headers=owner["headers"])
        assert receipt.status_code == 200
        assert receipt.headers["content-type"] == "application/pdf"
