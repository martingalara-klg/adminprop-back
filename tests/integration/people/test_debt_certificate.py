"""tests/integration/people/test_debt_certificate.py -- issue #24.

SDD: spec_module_04_cobranzas.md §RF-08 + sdd_03 §9
`POST /renters/:id/debt-certificate`.
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


async def _set_billing_header(organization_id, *, name: str) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        result = await session.execute(
            sa.text("SELECT settings FROM organizations WHERE id = :id"),
            {"id": str(organization_id)},
        )
        raw_settings = result.scalar_one()
        settings = json.loads(raw_settings) if isinstance(raw_settings, str) else dict(raw_settings)
        settings["billing_header"] = {"name": name, "cuit": None, "contact": None}
        await session.execute(
            sa.text("UPDATE organizations SET settings = :settings WHERE id = :id").bindparams(
                sa.bindparam("settings", type_=sa.JSON)
            ),
            {"id": str(organization_id), "settings": json.dumps(settings)},
        )


def _pdf_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


async def _seed_renter_with_contract(seed, *, renter_name: str = "Carlos Diaz"):
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
        address="Bv. San Juan 500",
    )
    contract_id = await seed.create_contract_row(
        organization_id=org["organization_id"], property_id=property_id, renter_id=renter_id
    )
    return org, owner, renter_id, contract_id


class TestDebtCertificate:
    """CA-04-11: inquilino sin deuda obtiene su certificado de libre deuda
    en PDF, y la emision queda auditada.
    CA-04-12: inquilino con periodos impagos o saldos parciales recibe
    `422 RENTER_HAS_DEBT` con el detalle de lo adeudado."""

    async def test_ca_04_11_renter_without_debt_gets_certificate_and_it_is_audited(
        self, client, seed
    ):
        org, owner, renter_id, contract_id = await _seed_renter_with_contract(
            seed, renter_name="Carlos Diaz"
        )
        await _set_billing_header(org["organization_id"], name="Administradora Ejemplo SRL")
        await seed.create_rent_period_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            amount_due="1000.00",
            paid_total="1000.00",
            status="paid",
        )

        response = await client.post(
            f"/v1/renters/{renter_id}/debt-certificate", headers=owner["headers"]
        )

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert "attachment" in response.headers["content-disposition"]
        assert response.content.startswith(b"%PDF-")

        text = _pdf_text(response.content)
        assert "Certificado de libre deuda" in text
        assert "Carlos Diaz" in text
        assert "Administradora Ejemplo SRL" in text
        assert "Bv. San Juan 500" in text

        rows = await seed.audit_rows(org["organization_id"], "debt_certificate.issued")
        assert len(rows) == 1
        assert str(rows[0]["entity_id"]) == str(renter_id)
        assert str(rows[0]["user_id"]) == str(owner["id"])

    async def test_ca_04_12_renter_with_unpaid_period_returns_422_renter_has_debt(
        self, client, seed
    ):
        org, owner, renter_id, contract_id = await _seed_renter_with_contract(seed)
        await seed.create_rent_period_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            amount_due="1000.00",
            paid_total="0.00",
            status="pending",
        )

        response = await client.post(
            f"/v1/renters/{renter_id}/debt-certificate", headers=owner["headers"]
        )

        assert response.status_code == 422
        body = response.json()["error"]
        assert body["code"] == "RENTER_HAS_DEBT"
        assert len(body["details"]["debts"]) == 1
        assert body["details"]["debts"][0]["contract_id"] == str(contract_id)

    async def test_ca_04_12_renter_with_partial_balance_returns_422_renter_has_debt(
        self, client, seed
    ):
        org, owner, renter_id, contract_id = await _seed_renter_with_contract(seed)
        await seed.create_rent_period_row(
            organization_id=org["organization_id"],
            contract_id=contract_id,
            amount_due="1000.00",
            paid_total="300.00",
            status="partial",
        )

        response = await client.post(
            f"/v1/renters/{renter_id}/debt-certificate", headers=owner["headers"]
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "RENTER_HAS_DEBT"

    async def test_debt_certificate_for_nonexistent_renter_returns_404(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
        )

        response = await client.post(
            f"/v1/renters/{uuid.uuid4()}/debt-certificate", headers=owner["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_debt_certificate_of_another_organization_returns_404(self, client, seed):
        """RN-D01: aislamiento cross-tenant -- 404, no 403."""
        org_a = await seed.create_organization_with_system_roles(name="Org A")
        owner_a = await seed.add_member(
            organization_id=org_a["organization_id"],
            role_id=org_a["roles"]["owner"],
            role_name="owner",
        )
        org_b = await seed.create_organization_with_system_roles(name="Org B")
        renter_b_id = await seed.create_renter_row(organization_id=org_b["organization_id"])

        response = await client.post(
            f"/v1/renters/{renter_b_id}/debt-certificate", headers=owner_a["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_maintenance_role_cannot_issue_debt_certificate(self, client, seed):
        """RN-A01: `maintenance` no tiene `renter:read`."""
        org = await seed.create_organization_with_system_roles()
        maintenance = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["maintenance"],
            role_name="maintenance",
        )
        renter_id = await seed.create_renter_row(organization_id=org["organization_id"])

        response = await client.post(
            f"/v1/renters/{renter_id}/debt-certificate", headers=maintenance["headers"]
        )

        assert response.status_code == 403
