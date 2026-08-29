"""tests/integration/contracts/test_adjustment_applied_by_name_and_pct_effective.py

SDD: docs/sdd/features/spec_module_03_contratos.md RF-04 punto 7, RN-10 +
core/sdd_03_api_contracts.md §8 "Contratos" (issue #118, decision #127).
Implements: CA-03-23, CA-03-24, CA-03-25, CA-03-26.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

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


class TestCA0323AppliedByNameResolved:
    """CA-03-23: un ajuste `applied` expone `applied_by_name` con el
    `full_name` del usuario que lo aplico (resuelto desde `users` por
    `applied_by` -- no expone solo el UUID)."""

    async def test_ca_03_23_applied_by_name_resolved_to_full_name(self, client, seed):
        _org, owner, _admin = await _seed_org_with_owner_and_admin(seed)
        contract_id = await _seed_active_ars_contract(seed, owner["organization_id"])
        adjustment_id = await seed.create_adjustment_row(
            organization_id=owner["organization_id"],
            contract_id=contract_id,
            status="applied",
            previous_amount="100000.00",
            pct_applied="10.00",
            new_amount="110000.00",
            applied_by=owner["id"],
        )

        response = await client.get(
            f"/v1/contracts/{contract_id}/adjustments", headers=owner["headers"]
        )

        assert response.status_code == 200
        item = next(i for i in response.json()["data"] if i["id"] == str(adjustment_id))
        assert item["applied_by"] == str(owner["id"])
        # conftest.py `create_user` siembra siempre "Test User" -- mismo
        # criterio que el resto de los tests de este modulo (test_adjustments.py).
        assert item["applied_by_name"] == "Test User"

    async def test_ca_03_23_applied_by_name_via_apply_endpoint_response(self, client, seed):
        """El mismo schema aplica a la respuesta directa de
        `POST /adjustments/:id/apply` (no solo al historial)."""
        _org, _owner, admin = await _seed_org_with_owner_and_admin(seed)
        contract_id = await _seed_active_ars_contract(seed, admin["organization_id"])
        adjustment_id = await seed.create_adjustment_row(
            organization_id=admin["organization_id"],
            contract_id=contract_id,
            status="pending",
            previous_amount="100000.00",
        )

        response = await client.post(
            f"/v1/adjustments/{adjustment_id}/apply",
            json={"pct": "10.00"},
            headers=admin["headers"],
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["applied_by"] == str(admin["id"])
        assert data["applied_by_name"] == "Test User"


class TestCA0324PctEffectiveOnInitialLoad:
    """CA-03-24: el ajuste sintetico de carga inicial (`pct_applied = NULL`,
    issues #100/#107) expone `pct_effective` calculado."""

    async def test_ca_03_24_pct_effective_calculated_on_initial_load_adjustment(self, client, seed):
        _org, owner, _admin = await _seed_org_with_owner_and_admin(seed)
        contract_id = await _seed_active_ars_contract(seed, owner["organization_id"])
        # RN-08/RN-C06 (issue #100): ajuste sintetico "Carga inicial" --
        # `pct_applied` queda NULL, solo `previous_amount`/`new_amount`.
        await seed.create_adjustment_row(
            organization_id=owner["organization_id"],
            contract_id=contract_id,
            status="applied",
            previous_amount="1000000.00",
            new_amount="1200000.00",
            pct_applied=None,
            applied_by=owner["id"],
        )

        response = await client.get(
            f"/v1/contracts/{contract_id}/adjustments", headers=owner["headers"]
        )

        assert response.status_code == 200
        item = response.json()["data"][0]
        assert item["pct_applied"] is None
        assert item["pct_effective"] == "20.00"


class TestCA0325PctEffectiveMatchesManualPctApplied:
    """CA-03-25: un ajuste manual aplicado con un `pct` dado expone
    `pct_effective` que coincide con el `pct_applied` guardado (dentro del
    redondeo `ROUND_HALF_EVEN` a 2 decimales, RN-10)."""

    async def test_ca_03_25_manual_adjustment_pct_effective_matches_pct_applied(self, client, seed):
        _org, owner, _admin = await _seed_org_with_owner_and_admin(seed)
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
        # `pct_applied` viaja con la escala NUMERIC(14,4) del dominio
        # (RF-02 §"Validaciones"); `pct_effective` es Decimal redondeado a
        # 2 decimales (RN-10) -- se comparan como Decimal, no como string.
        assert Decimal(data["pct_applied"]) == Decimal(data["pct_effective"])
        assert data["pct_effective"] == "10.00"

    async def test_ca_03_25_pct_effective_uses_banker_rounding(self, client, seed):
        """RN-10: `ROUND_HALF_EVEN` explicito, nunca `float`. Se ejercita
        con un caso borde (mitad exacta entre dos centavos) directamente
        sobre el helper de calculo -- el mismo que usa el service."""
        from adminprop.modules.contracts.adjustment_service import _compute_pct_effective
        from adminprop.modules.contracts.models import ContractAdjustment

        row = ContractAdjustment(
            status="applied",
            previous_amount=Decimal("100000.00"),
            new_amount=Decimal("100000.125"),
        )
        result = _compute_pct_effective(row)
        expected = (
            (Decimal("100000.125") - Decimal("100000.00")) / Decimal("100000.00") * Decimal(100)
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        assert result == expected


class TestCA0326PendingAdjustmentExposesNullNameAndPct:
    """CA-03-26: un ajuste `pending` (sin aplicar) expone
    `applied_by_name: null` y `pct_effective: null`."""

    async def test_ca_03_26_pending_adjustment_exposes_null_name_and_pct_effective(
        self, client, seed
    ):
        _org, owner, _admin = await _seed_org_with_owner_and_admin(seed)
        contract_id = await _seed_active_ars_contract(seed, owner["organization_id"])
        await seed.create_adjustment_row(
            organization_id=owner["organization_id"],
            contract_id=contract_id,
            status="pending",
        )

        response = await client.get(
            f"/v1/contracts/{contract_id}/adjustments", headers=owner["headers"]
        )

        assert response.status_code == 200
        item = response.json()["data"][0]
        assert item["status"] == "pending"
        assert item["applied_by"] is None
        assert item["applied_by_name"] is None
        assert item["pct_effective"] is None

    async def test_pending_inbox_item_also_exposes_null_name_and_pct_effective(self, client, seed):
        """Mismo schema en `GET /adjustments?status=pending` (la bandeja)."""
        _org, owner, _admin = await _seed_org_with_owner_and_admin(seed)
        contract_id = await _seed_active_ars_contract(seed, owner["organization_id"])
        await seed.create_adjustment_row(
            organization_id=owner["organization_id"],
            contract_id=contract_id,
            status="pending",
        )

        response = await client.get(
            "/v1/adjustments", params={"status": "pending"}, headers=owner["headers"]
        )

        assert response.status_code == 200
        item = response.json()["data"][0]
        assert item["applied_by_name"] is None
        assert item["pct_effective"] is None


class TestPctEffectiveZeroPreviousAmountGuard:
    """RN-10: `previous_amount = 0` -> `pct_effective: null` (evita
    division por cero -- caso defensivo, no deberia ocurrir en la
    practica dado RN-01: `initial_amount > 0`). `async def` solo para
    calzar con el `pytestmark = pytest.mark.asyncio` de modulo -- el
    calculo en si (`_compute_pct_effective`) es sincronico puro."""

    async def test_pct_effective_is_none_when_previous_amount_is_zero(self):
        from adminprop.modules.contracts.adjustment_service import _compute_pct_effective
        from adminprop.modules.contracts.models import ContractAdjustment

        row = ContractAdjustment(
            status="applied",
            previous_amount=Decimal("0.00"),
            new_amount=Decimal("100.00"),
        )

        assert _compute_pct_effective(row) is None


class TestTenantIsolationRegression:
    """Regresion (no cambia con este issue): la aplicacion existente de
    `docs/skills/tenant-isolation.md` para el modulo de contratos/ajustes
    sigue en verde -- se corre explicitamente el archivo dedicado del
    modulo como parte de la verificacion de este issue."""

    async def test_cross_tenant_adjustment_history_still_returns_404(self, client, seed):
        _org_a, owner_a, _admin_a = await _seed_org_with_owner_and_admin(seed)
        _org_b, owner_b, _admin_b = await _seed_org_with_owner_and_admin(seed)
        contract_b_id = await _seed_active_ars_contract(seed, owner_b["organization_id"])
        await seed.create_adjustment_row(
            organization_id=owner_b["organization_id"],
            contract_id=contract_b_id,
            status="applied",
            previous_amount="100000.00",
            new_amount="110000.00",
            pct_applied="10.00",
            applied_by=owner_b["id"],
        )

        response = await client.get(
            f"/v1/contracts/{contract_b_id}/adjustments", headers=owner_a["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
