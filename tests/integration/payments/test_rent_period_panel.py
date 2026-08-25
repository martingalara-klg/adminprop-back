"""tests/integration/payments/test_rent_period_panel.py -- issue #23.

SDD: spec_module_04_cobranzas.md §RF-02 + sdd_03 §9 `GET /rent-periods`,
`GET /rent-periods/:id`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest

pytestmark = pytest.mark.asyncio


def _previous_month(today: date) -> date:
    """Un mes calendario completo antes de `today`, normalizado al dia 1
    -- garantiza `days_late > 0` con el `grace_day` default (10) sin
    importar que dia del mes corre el test (a diferencia de "este mes",
    donde el resultado dependeria de si ya paso el dia 10)."""
    if today.month == 1:
        return date(today.year - 1, 12, 1)
    return date(today.year, today.month - 1, 1)


async def _seed_contract(seed, *, daily_late_fee_pct: str = "1.0"):
    org = await seed.create_organization_with_system_roles()
    owner = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["owner"],
        role_name="owner",
    )
    landlord_id = await seed.create_landlord_row(organization_id=org["organization_id"])
    renter_id = await seed.create_renter_row(organization_id=org["organization_id"])
    property_id = await seed.create_property_row(
        organization_id=org["organization_id"], landlord_id=landlord_id
    )
    contract_id = await seed.create_contract_row(
        organization_id=org["organization_id"],
        property_id=property_id,
        renter_id=renter_id,
        daily_late_fee_pct=daily_late_fee_pct,
    )
    return org, owner, landlord_id, renter_id, property_id, contract_id


class TestRentPeriodPanelList:
    """RF-02: `GET /rent-periods?period=YYYY-MM&status=&in_arrears=true`."""

    async def test_panel_row_shows_property_renter_amount_balance_and_arrears(self, client, seed):
        org, owner, landlord_id, renter_id, property_id, contract_id = await _seed_contract(seed)
        today = datetime.now(tz=UTC).date()
        period = _previous_month(today)
        rent_period_id = await seed.create_rent_period_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            period=period.isoformat(),
            amount_due="1000.00",
            paid_total="400.00",
            status="partial",
        )

        response = await client.get("/v1/rent-periods", headers=owner["headers"])

        assert response.status_code == 200
        rows = {item["id"]: item for item in response.json()["data"]}
        row = rows[str(rent_period_id)]
        assert row["property_id"] == str(property_id)
        assert row["renter_id"] == str(renter_id)
        assert row["landlord_id"] == str(landlord_id)
        assert row["amount_due"] == "1000.00"
        assert row["balance"] == "600.00"
        assert row["status"] == "partial"
        assert row["in_arrears"] is True
        assert row["days_late"] > 0
        assert float(row["suggested_interest"]) > 0

    async def test_filter_by_period_only_returns_that_months_periods(self, client, seed):
        org, owner, _, _, _, contract_id = await _seed_contract(seed)
        today = datetime.now(tz=UTC).date()
        current_period = date(today.year, today.month, 1)
        previous_period = _previous_month(today)
        current_id = await seed.create_rent_period_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            period=current_period.isoformat(),
        )
        await seed.create_rent_period_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            period=previous_period.isoformat(),
        )

        response = await client.get(
            "/v1/rent-periods",
            params={"period": current_period.strftime("%Y-%m")},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        ids = {item["id"] for item in response.json()["data"]}
        assert ids == {str(current_id)}

    async def test_filter_by_status(self, client, seed):
        org, owner, _, _, _, contract_id = await _seed_contract(seed)
        paid_id = await seed.create_rent_period_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            period="2026-03-01",
            amount_due="1000.00",
            paid_total="1000.00",
            status="paid",
        )
        await seed.create_rent_period_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            period="2026-04-01",
            status="pending",
        )

        response = await client.get(
            "/v1/rent-periods", params={"status": "paid"}, headers=owner["headers"]
        )

        assert response.status_code == 200
        ids = {item["id"] for item in response.json()["data"]}
        assert ids == {str(paid_id)}

    async def test_filter_in_arrears_true_excludes_periods_within_grace_day(self, client, seed):
        org, owner, _, _, _, contract_id = await _seed_contract(seed)
        today = datetime.now(tz=UTC).date()
        overdue_id = await seed.create_rent_period_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            period=_previous_month(today).isoformat(),
            status="pending",
        )

        response = await client.get(
            "/v1/rent-periods", params={"in_arrears": "true"}, headers=owner["headers"]
        )

        assert response.status_code == 200
        rows = response.json()["data"]
        ids = {item["id"] for item in rows}
        assert str(overdue_id) in ids
        assert all(item["in_arrears"] for item in rows)

    async def test_filter_by_property_landlord_renter(self, client, seed):
        org, owner, landlord_id, renter_id, property_id, contract_id = await _seed_contract(seed)
        rent_period_id = await seed.create_rent_period_row(
            organization_id=org["organization_id"], contract_id=contract_id
        )
        # Otro contrato/propiedad/inquilino de la MISMA organizacion, para
        # verificar que el filtro efectivamente discrimina (no solo tenant).
        other_landlord_id = await seed.create_landlord_row(organization_id=org["organization_id"])
        other_renter_id = await seed.create_renter_row(organization_id=org["organization_id"])
        other_property_id = await seed.create_property_row(
            organization_id=org["organization_id"], landlord_id=other_landlord_id
        )
        other_contract_id = await seed.create_contract_row(
            organization_id=org["organization_id"],
            property_id=other_property_id,
            renter_id=other_renter_id,
        )
        await seed.create_rent_period_row(
            organization_id=org["organization_id"], contract_id=other_contract_id
        )

        by_property = await client.get(
            "/v1/rent-periods", params={"property_id": str(property_id)}, headers=owner["headers"]
        )
        by_landlord = await client.get(
            "/v1/rent-periods", params={"landlord_id": str(landlord_id)}, headers=owner["headers"]
        )
        by_renter = await client.get(
            "/v1/rent-periods", params={"renter_id": str(renter_id)}, headers=owner["headers"]
        )

        for response in (by_property, by_landlord, by_renter):
            assert response.status_code == 200
            ids = {item["id"] for item in response.json()["data"]}
            assert ids == {str(rent_period_id)}

    async def test_invalid_period_format_returns_400_validation_error(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )

        response = await client.get(
            "/v1/rent-periods", params={"period": "2026/06"}, headers=owner["headers"]
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_maintenance_role_cannot_list_panel(self, client, seed):
        """RN-A01: `maintenance` no tiene `rent-period:read`."""
        org = await seed.create_organization_with_system_roles()
        maintenance = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["maintenance"],
            role_name="maintenance",
        )

        response = await client.get("/v1/rent-periods", headers=maintenance["headers"])

        assert response.status_code == 403

    async def test_cursor_pagination_returns_all_rows_across_pages_without_duplicates(
        self, client, seed
    ):
        """sdd_03 §"Paginacion": cursor-based -- `limit=1` sobre 2 filas
        debe paginar sin duplicar ni perder ninguna."""
        org, owner, _, _, _, contract_id = await _seed_contract(seed)
        first_id = await seed.create_rent_period_row(
            organization_id=org["organization_id"], contract_id=contract_id, period="2026-01-01"
        )
        second_id = await seed.create_rent_period_row(
            organization_id=org["organization_id"], contract_id=contract_id, period="2026-02-01"
        )

        page_1 = await client.get("/v1/rent-periods", params={"limit": 1}, headers=owner["headers"])
        assert page_1.status_code == 200
        assert len(page_1.json()["data"]) == 1
        next_cursor = page_1.json()["meta"]["next_cursor"]
        assert next_cursor is not None

        page_2 = await client.get(
            "/v1/rent-periods",
            params={"limit": 1, "cursor": next_cursor},
            headers=owner["headers"],
        )
        assert page_2.status_code == 200
        assert len(page_2.json()["data"]) == 1
        assert page_2.json()["meta"]["next_cursor"] is None

        seen_ids = {page_1.json()["data"][0]["id"], page_2.json()["data"][0]["id"]}
        assert seen_ids == {str(first_id), str(second_id)}

    async def test_panel_list_rows_do_not_include_payments(self, client, seed):
        """issue #87: `GET /rent-periods` (panel/listado) NO cambia -- solo
        `GET /rent-periods/:id` embebe `payments[]`."""
        org, owner, _, _, _, contract_id = await _seed_contract(seed)
        rent_period_id = await seed.create_rent_period_row(
            organization_id=org["organization_id"], contract_id=contract_id
        )
        await seed.create_payment_row(
            organization_id=org["organization_id"],
            rent_period_id=rent_period_id,
            created_by=owner["id"],
        )

        response = await client.get("/v1/rent-periods", headers=owner["headers"])

        assert response.status_code == 200
        row = next(item for item in response.json()["data"] if item["id"] == str(rent_period_id))
        assert "payments" not in row


class TestRentPeriodDetail:
    """RF-02: `GET /rent-periods/:id`."""

    async def test_get_rent_period_detail_returns_same_shape_as_panel_row(self, client, seed):
        org, owner, _, _, _, contract_id = await _seed_contract(seed)
        rent_period_id = await seed.create_rent_period_row(
            organization_id=org["organization_id"], contract_id=contract_id
        )

        response = await client.get(f"/v1/rent-periods/{rent_period_id}", headers=owner["headers"])

        assert response.status_code == 200
        assert response.json()["data"]["id"] == str(rent_period_id)

    async def test_get_nonexistent_rent_period_returns_404(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )

        response = await client.get(f"/v1/rent-periods/{uuid.uuid4()}", headers=owner["headers"])

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_detail_with_no_payments_returns_empty_list(self, client, seed):
        """issue #87: periodo sin cobros -> `payments: []`."""
        org, owner, _, _, _, contract_id = await _seed_contract(seed)
        rent_period_id = await seed.create_rent_period_row(
            organization_id=org["organization_id"], contract_id=contract_id
        )

        response = await client.get(f"/v1/rent-periods/{rent_period_id}", headers=owner["headers"])

        assert response.status_code == 200
        assert response.json()["data"]["payments"] == []

    async def test_detail_returns_multiple_payments_ordered_by_payment_date(self, client, seed):
        """issue #87: `payments[]` ordenados por `payment_date` ascendente."""
        org, owner, _, _, _, contract_id = await _seed_contract(seed)
        rent_period_id = await seed.create_rent_period_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            amount_due="3000.00",
            status="partial",
        )
        # Insertados fuera de orden a proposito -- la respuesta debe
        # quedar ordenada por payment_date, no por orden de insercion.
        later_id = await seed.create_payment_row(
            organization_id=org["organization_id"],
            rent_period_id=rent_period_id,
            created_by=owner["id"],
            payment_date="2026-06-20",
            amount="1000.00",
        )
        earlier_id = await seed.create_payment_row(
            organization_id=org["organization_id"],
            rent_period_id=rent_period_id,
            created_by=owner["id"],
            payment_date="2026-06-05",
            amount="500.00",
        )

        response = await client.get(f"/v1/rent-periods/{rent_period_id}", headers=owner["headers"])

        assert response.status_code == 200
        payments = response.json()["data"]["payments"]
        assert [p["id"] for p in payments] == [str(earlier_id), str(later_id)]
        assert payments[0]["payment_date"] == "2026-06-05"
        assert payments[1]["payment_date"] == "2026-06-20"

    async def test_detail_includes_voided_payment_with_voided_at_and_voided_by(self, client, seed):
        """CA-04-07: "el cobro queda visible con marca de anulado" --
        verificable por API (issue #87)."""
        org, owner, _, _, _, contract_id = await _seed_contract(seed)
        rent_period_id = await seed.create_rent_period_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            amount_due="1000.00",
            paid_total="1000.00",
            status="paid",
        )
        payment_id = await seed.create_payment_row(
            organization_id=org["organization_id"],
            rent_period_id=rent_period_id,
            created_by=owner["id"],
            amount="1000.00",
        )
        void_response = await client.post(
            f"/v1/payments/{payment_id}/void",
            json={"reason": "Cobro duplicado por error de carga"},
            headers=owner["headers"],
        )
        assert void_response.status_code == 200

        response = await client.get(f"/v1/rent-periods/{rent_period_id}", headers=owner["headers"])

        assert response.status_code == 200
        payments = response.json()["data"]["payments"]
        assert len(payments) == 1
        assert payments[0]["id"] == str(payment_id)
        assert payments[0]["voided_at"] is not None
        assert payments[0]["voided_by"] == str(owner["id"])
