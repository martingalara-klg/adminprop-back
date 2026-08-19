"""tests/integration/payments/test_receipt.py -- issue #24.

SDD: spec_module_04_cobranzas.md §RF-07 + sdd_03 §9
`GET /payments/:id/receipt`.
"""

from __future__ import annotations

import json
import uuid
from io import BytesIO

import pytest
import sqlalchemy as sa
from pypdf import PdfReader

from adminprop.db.session import get_session_factory

pytestmark = pytest.mark.asyncio


async def _set_billing_header(organization_id, *, name: str, cuit: str, contact: str) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        result = await session.execute(
            sa.text("SELECT settings FROM organizations WHERE id = :id"),
            {"id": str(organization_id)},
        )
        raw_settings = result.scalar_one()
        settings = json.loads(raw_settings) if isinstance(raw_settings, str) else dict(raw_settings)
        settings["billing_header"] = {"name": name, "cuit": cuit, "contact": contact}
        await session.execute(
            sa.text("UPDATE organizations SET settings = :settings WHERE id = :id").bindparams(
                sa.bindparam("settings", type_=sa.JSON)
            ),
            {"id": str(organization_id), "settings": json.dumps(settings)},
        )


def _pdf_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


async def _seed_payment(
    seed,
    *,
    payment_currency: str = "ARS",
    exchange_rate: str | None = None,
    amount: str = "500.00",
    charged_interest: str = "50.00",
    renter_name: str = "Juana Perez",
    property_address: str = "Av. Colon 1234",
    voided: bool = False,
):
    org = await seed.create_organization_with_system_roles()
    owner = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["owner"],
        role_name="owner",
    )
    landlord_id = await seed.create_landlord_row(organization_id=org["organization_id"])
    renter_id = await seed.create_renter_row(
        organization_id=org["organization_id"], name=renter_name
    )
    property_id = await seed.create_property_row(
        organization_id=org["organization_id"],
        landlord_id=landlord_id,
        address=property_address,
    )
    contract_id = await seed.create_contract_row(
        organization_id=org["organization_id"],
        property_id=property_id,
        renter_id=renter_id,
        currency="ARS",
    )
    rent_period_id = await seed.create_rent_period_row(
        organization_id=org["organization_id"],
        contract_id=contract_id,
        amount_due="1000.00",
        paid_total=amount,
        status="partial",
    )
    payment_id = await seed.create_payment_row(
        organization_id=org["organization_id"],
        rent_period_id=rent_period_id,
        created_by=owner["id"],
        amount=amount,
        payment_currency=payment_currency,
        charged_interest=charged_interest,
    )
    if exchange_rate is not None:
        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await session.execute(
                sa.text("UPDATE payments SET exchange_rate = :rate WHERE id = :id"),
                {"rate": exchange_rate, "id": str(payment_id)},
            )
    if voided:
        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await session.execute(
                sa.text("UPDATE payments SET voided_at = now(), voided_by = :by WHERE id = :id"),
                {"by": str(owner["id"]), "id": str(payment_id)},
            )
    return org, owner, payment_id


class TestPaymentReceipt:
    """CA-04-10: tras registrar un cobro se puede descargar su recibo PDF
    con capital, interes, TC (si aplico) y el encabezado de la
    administradora; un cobro anulado no emite recibo."""

    async def test_ca_04_10_downloads_receipt_pdf_with_capital_interest_and_header(
        self, client, seed
    ):
        org, owner, payment_id = await _seed_payment(
            seed, renter_name="Juana Perez", property_address="Av. Colon 1234"
        )
        await _set_billing_header(
            org["organization_id"],
            name="Administradora Ejemplo SRL",
            cuit="20111111112",
            contact="contacto@ejemplo.com",
        )

        response = await client.get(f"/v1/payments/{payment_id}/receipt", headers=owner["headers"])

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert "attachment" in response.headers["content-disposition"]
        assert response.content.startswith(b"%PDF-")

        text = _pdf_text(response.content)
        assert "Recibo de cobro" in text
        assert "Administradora Ejemplo SRL" in text
        assert "Juana Perez" in text
        assert "Av. Colon 1234" in text
        assert "500.00 ARS" in text
        assert "50.00 ARS" in text

    async def test_ca_04_10_receipt_includes_exchange_rate_when_payment_currency_differs(
        self, client, seed
    ):
        _org, owner, payment_id = await _seed_payment(
            seed, payment_currency="USD", exchange_rate="1000.0000"
        )

        response = await client.get(f"/v1/payments/{payment_id}/receipt", headers=owner["headers"])

        assert response.status_code == 200
        text = _pdf_text(response.content)
        assert "USD" in text
        assert "1000.0000" in text

    async def test_ca_04_10_voided_payment_does_not_emit_receipt(self, client, seed):
        """RF-07: "sobre un cobro anulado no se emite recibo (`422
        BUSINESS_RULE_VIOLATION`)"."""
        _org, owner, payment_id = await _seed_payment(seed, voided=True)

        response = await client.get(f"/v1/payments/{payment_id}/receipt", headers=owner["headers"])

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "BUSINESS_RULE_VIOLATION"

    async def test_receipt_of_nonexistent_payment_returns_404(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )

        response = await client.get(
            f"/v1/payments/{uuid.uuid4()}/receipt", headers=owner["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_maintenance_role_cannot_download_receipt(self, client, seed):
        """RN-A01: `maintenance` no tiene `rent-period:read`."""
        org = await seed.create_organization_with_system_roles()
        maintenance = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["maintenance"],
            role_name="maintenance",
        )
        landlord_id = await seed.create_landlord_row(organization_id=org["organization_id"])
        renter_id = await seed.create_renter_row(organization_id=org["organization_id"])
        property_id = await seed.create_property_row(
            organization_id=org["organization_id"], landlord_id=landlord_id
        )
        contract_id = await seed.create_contract_row(
            organization_id=org["organization_id"], property_id=property_id, renter_id=renter_id
        )
        rent_period_id = await seed.create_rent_period_row(
            organization_id=org["organization_id"], contract_id=contract_id
        )
        payment_id = await seed.create_payment_row(
            organization_id=org["organization_id"],
            rent_period_id=rent_period_id,
            created_by=maintenance["id"],
        )

        response = await client.get(
            f"/v1/payments/{payment_id}/receipt", headers=maintenance["headers"]
        )

        assert response.status_code == 403
