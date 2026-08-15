"""tests/integration/notifications/test_emit_and_outbox.py

SDD: infrastructure/spec_notificaciones.md RF-01, RN-01, RN-02, RN-03.
Implements: CA-NT-02, CA-NT-05.

CA-NT-01/CA-NT-04 (panel in-app completo) son del issue #31 -- este
issue testea el enrutamiento por rol (RN-01) con dos eventos reales del
catalogo (`work_order_created` -> maintenance, `quote_submitted` ->
owner+admin), declarado explicitamente segun lo pedido: los emisores de
negocio reales (ajustes, vencimientos, mantenimiento) llegan en las
fases 4-6, asi que no hay todavia un flujo HTTP real que dispare
`emit()` -- se invoca la funcion del servicio directamente con datos
sembrados via el fixture `seed`.
"""

from __future__ import annotations

import uuid

import pytest

from adminprop.db.session import get_session_factory, set_tenant_context
from adminprop.shared.notifications.service import emit

pytestmark = pytest.mark.asyncio


class TestEmitCreatesInAppRowsInSameTransaction:
    """RF-01/RN-02: una fila in-app por destinatario, en la misma sesion."""

    async def test_emit_work_order_created_notifies_only_maintenance_role(
        self, seed, notifications_reader
    ):
        """RN-01 (tabla de enrutamiento): `work_order_created` -> solo
        usuarios con rol `maintenance`, ni owner ni admin."""
        org = await seed.create_org_with_roles()
        maintenance_user = await seed.add_member(
            organization_id=org["organization_id"], role_id=org["roles"]["maintenance"]
        )
        owner_user = await seed.add_member(
            organization_id=org["organization_id"], role_id=org["roles"]["owner"]
        )

        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await set_tenant_context(session, org["organization_id"])
            notification_ids = await emit(
                session,
                organization_id=org["organization_id"],
                event_type="work_order_created",
                payload={"work_order_id": str(uuid.uuid4())},
            )

        assert len(notification_ids) == 1
        rows = await notifications_reader(org["organization_id"])
        recipient_ids = {row["user_id"] for row in rows}
        assert recipient_ids == {maintenance_user["id"]}
        assert owner_user["id"] not in recipient_ids
        assert rows[0]["event_type"] == "work_order_created"
        assert rows[0]["email_sent_at"] is None

    async def test_emit_quote_submitted_notifies_owner_and_admin_not_maintenance(
        self, seed, notifications_reader
    ):
        """RN-01: `quote_submitted` -> owner + admin, nunca maintenance."""
        org = await seed.create_org_with_roles()
        owner_user = await seed.add_member(
            organization_id=org["organization_id"], role_id=org["roles"]["owner"]
        )
        admin_user = await seed.add_member(
            organization_id=org["organization_id"], role_id=org["roles"]["admin"]
        )
        maintenance_user = await seed.add_member(
            organization_id=org["organization_id"], role_id=org["roles"]["maintenance"]
        )

        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await set_tenant_context(session, org["organization_id"])
            notification_ids = await emit(
                session,
                organization_id=org["organization_id"],
                event_type="quote_submitted",
                payload={"work_order_id": str(uuid.uuid4())},
            )

        assert len(notification_ids) == 2
        rows = await notifications_reader(org["organization_id"])
        recipient_ids = {row["user_id"] for row in rows}
        assert recipient_ids == {owner_user["id"], admin_user["id"]}
        assert maintenance_user["id"] not in recipient_ids


class TestCaNt02RollbackLeavesNoNotification:
    """CA-NT-02: "Si el alta del pedido de reparación falla a mitad de
    transacción, no queda ninguna notificación creada."."""

    async def test_ca_nt_02_business_rollback_leaves_no_notification(
        self, seed, notifications_reader
    ):
        org = await seed.create_org_with_roles()
        await seed.add_member(
            organization_id=org["organization_id"], role_id=org["roles"]["maintenance"]
        )

        session_factory = get_session_factory()
        # Sin `session.begin()` automatico: se simula el fallo de la
        # operacion de negocio con un rollback EXPLICITO en vez de un
        # commit, igual que pediria un `except` real en el caller.
        async with session_factory() as session:
            await set_tenant_context(session, org["organization_id"])
            notification_ids = await emit(
                session,
                organization_id=org["organization_id"],
                event_type="work_order_created",
                payload={"work_order_id": str(uuid.uuid4())},
            )
            assert len(notification_ids) == 1  # la fila existe DENTRO de la transaccion...
            await session.rollback()  # ...pero la operacion de negocio "fallo"

        rows = await notifications_reader(org["organization_id"])
        assert rows == []  # ...y no quedo ninguna notificacion persistida.


class TestCaNt05InactiveUserDoesNotReceiveNotifications:
    """CA-NT-05: "Un usuario desactivado no recibe nuevas notificaciones."."""

    async def test_ca_nt_05_inactive_member_is_excluded_from_recipients(
        self, seed, notifications_reader
    ):
        org = await seed.create_org_with_roles()
        active_owner = await seed.add_member(
            organization_id=org["organization_id"], role_id=org["roles"]["owner"]
        )
        inactive_owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            status="inactive",
        )

        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await set_tenant_context(session, org["organization_id"])
            await emit(
                session,
                organization_id=org["organization_id"],
                event_type="contract_expiring",
                payload={"contract_id": str(uuid.uuid4())},
            )

        rows = await notifications_reader(org["organization_id"])
        recipient_ids = {row["user_id"] for row in rows}
        assert recipient_ids == {active_owner["id"]}
        assert inactive_owner["id"] not in recipient_ids


class TestEmitRejectsUnknownEventType:
    async def test_emit_raises_value_error_for_unknown_event_type(self, seed):
        org = await seed.create_org_with_roles()
        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await set_tenant_context(session, org["organization_id"])
            with pytest.raises(ValueError, match="event_type desconocido"):
                await emit(
                    session,
                    organization_id=org["organization_id"],
                    event_type="not_a_real_event",
                    payload={},
                )
