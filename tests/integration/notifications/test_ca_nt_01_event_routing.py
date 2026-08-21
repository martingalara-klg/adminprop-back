"""tests/integration/notifications/test_ca_nt_01_event_routing.py -- issue #31.

SDD: infrastructure/spec_notificaciones.md v1.1 "Eventos del MVP y
     enrutamiento por rol" (tabla completa de 6 eventos).
Implements: CA-NT-01: "Cada uno de los 6 eventos genera la notificación
            in-app a los destinatarios correctos según la tabla de
            enrutamiento (y a nadie más)."

`tests/integration/notifications/test_emit_and_outbox.py` (issue #11) ya
cubre `work_order_created`, `quote_submitted` y `contract_expiring` con
foco en RF-01/CA-NT-02/CA-NT-05 -- esta suite es la cobertura CANONICA de
CA-NT-01: parametriza los 6 eventos del MVP contra la tabla v1.1 completa
y verifica, para cada uno, tanto los destinatarios correctos COMO que
ningun otro rol de la organizacion recibe nada (el "y a nadie mas" del
criterio).
"""

from __future__ import annotations

import uuid

import pytest

from adminprop.db.session import get_session_factory, set_tenant_context
from adminprop.shared.notifications.service import emit

pytestmark = pytest.mark.asyncio

# spec_notificaciones.md v1.1 -- tabla "Eventos del MVP y enrutamiento por rol".
_EVENT_RECIPIENT_ROLES = {
    "adjustment_pending": {"owner", "admin"},
    "contract_expiring": {"owner", "admin"},
    "work_order_created": {"maintenance"},
    "quote_submitted": {"owner", "admin"},
    "quote_approved": {"maintenance"},
    "work_order_closed": {"owner", "admin"},
}
_ALL_ROLES = {"owner", "admin", "maintenance"}


@pytest.mark.parametrize("event_type", sorted(_EVENT_RECIPIENT_ROLES))
async def test_ca_nt_01_event_notifies_only_the_correct_roles(
    event_type, seed, notifications_reader
):
    """CA-NT-01 para `event_type`: los roles de la tabla v1.1 reciben la
    notificacion, y los roles NO listados no reciben nada."""
    org = await seed.create_org_with_roles()
    members_by_role = {
        role_name: await seed.add_member(
            organization_id=org["organization_id"], role_id=org["roles"][role_name]
        )
        for role_name in _ALL_ROLES
    }

    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, org["organization_id"])
        notification_ids = await emit(
            session,
            organization_id=org["organization_id"],
            event_type=event_type,
            payload={"work_order_id": str(uuid.uuid4())},
        )

    expected_roles = _EVENT_RECIPIENT_ROLES[event_type]
    expected_recipient_ids = {members_by_role[role]["id"] for role in expected_roles}
    excluded_roles = _ALL_ROLES - expected_roles
    excluded_recipient_ids = {members_by_role[role]["id"] for role in excluded_roles}

    assert len(notification_ids) == len(expected_recipient_ids)
    rows = await notifications_reader(org["organization_id"])
    recipient_ids = {row["user_id"] for row in rows}
    assert recipient_ids == expected_recipient_ids
    assert recipient_ids.isdisjoint(excluded_recipient_ids)
    assert all(row["event_type"] == event_type for row in rows)
