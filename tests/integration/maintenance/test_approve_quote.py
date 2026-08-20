"""tests/integration/maintenance/test_approve_quote.py -- issue #26.

SDD: spec_module_06_mantenimiento.md §RF-03. Covers: CA-06-03.

CONCERN (ver docstring de `modules/maintenance/service.py`): CA-06-03
pide "el encargado es notificado" al aprobarse la cotizacion, pero el
enum `notifications.event_type` (migracion #11) no tiene `quote_approved`
-- esta suite verifica la parte SI implementable (transicion de estado +
409 en re-aprobacion + auditoria) y deja explicito que la notificacion al
encargado NO se emite (divergencia documentada, no un olvido).
"""

from __future__ import annotations

import pytest


async def _seed_work_order_with_two_quotes(seed):
    org = await seed.create_organization_with_system_roles()
    owner = await seed.add_member(
        organization_id=org["organization_id"], role_id=org["roles"]["owner"], role_name="owner"
    )
    maintenance_user = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["maintenance"],
        role_name="maintenance",
    )
    property_id = await seed.create_property(organization_id=org["organization_id"])
    work_order_id = await seed.create_work_order_row(
        organization_id=org["organization_id"], property_id=property_id, created_by=owner["id"]
    )
    quote_a_id = await seed.create_quote_row(
        organization_id=org["organization_id"],
        work_order_id=work_order_id,
        submitted_by=maintenance_user["id"],
        amount="1500.00",
    )
    quote_b_id = await seed.create_quote_row(
        organization_id=org["organization_id"],
        work_order_id=work_order_id,
        submitted_by=maintenance_user["id"],
        amount="1800.00",
    )
    return org, owner, maintenance_user, work_order_id, quote_a_id, quote_b_id


class TestCA0603ApproveQuote:
    """CA-06-03: "Al aprobar una cotizacion, el pedido pasa a
    `in_progress`, la otra queda `discarded`... aprobar de nuevo devuelve
    `409 QUOTE_ALREADY_APPROVED`"."""

    @pytest.mark.asyncio
    async def test_ca_06_03_approving_a_quote_sets_work_order_in_progress_and_discards_others(
        self, client, seed
    ):
        (
            org,
            owner,
            _maintenance_user,
            _work_order_id,
            quote_a_id,
            quote_b_id,
        ) = await _seed_work_order_with_two_quotes(seed)

        response = await client.post(f"/v1/quotes/{quote_a_id}/approve", headers=owner["headers"])

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "in_progress"
        assert data["approved_quote_id"] == str(quote_a_id)
        assert data["final_cost"] == "1500.00"

        quote_a = await seed.get_quote(quote_a_id)
        quote_b = await seed.get_quote(quote_b_id)
        assert quote_a["status"] == "approved"
        assert quote_b["status"] == "discarded"

        audit = await seed.audit_rows(org["organization_id"], "work_order_quote.approved")
        assert len(audit) == 1
        assert audit[0]["entity_id"] == quote_a_id

    @pytest.mark.asyncio
    async def test_ca_06_03_reapproving_same_quote_returns_409_quote_already_approved(
        self, client, seed
    ):
        (
            _org,
            owner,
            _maintenance_user,
            _work_order_id,
            quote_a_id,
            _quote_b_id,
        ) = await _seed_work_order_with_two_quotes(seed)
        first = await client.post(f"/v1/quotes/{quote_a_id}/approve", headers=owner["headers"])
        assert first.status_code == 200

        second = await client.post(f"/v1/quotes/{quote_a_id}/approve", headers=owner["headers"])

        assert second.status_code == 409
        assert second.json()["error"]["code"] == "QUOTE_ALREADY_APPROVED"

    @pytest.mark.asyncio
    async def test_ca_06_03_approving_a_different_quote_after_one_approved_returns_409(
        self, client, seed
    ):
        (
            _org,
            owner,
            _maintenance_user,
            _work_order_id,
            quote_a_id,
            quote_b_id,
        ) = await _seed_work_order_with_two_quotes(seed)
        first = await client.post(f"/v1/quotes/{quote_a_id}/approve", headers=owner["headers"])
        assert first.status_code == 200

        second = await client.post(f"/v1/quotes/{quote_b_id}/approve", headers=owner["headers"])

        assert second.status_code == 409
        assert second.json()["error"]["code"] == "QUOTE_ALREADY_APPROVED"

    @pytest.mark.asyncio
    async def test_approve_unknown_quote_returns_404(self, client, seed):
        import uuid

        org = await seed.create_organization_with_system_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"], role_id=org["roles"]["owner"], role_name="owner"
        )

        response = await client.post(f"/v1/quotes/{uuid.uuid4()}/approve", headers=owner["headers"])

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
