"""tests/integration/notifications/test_tenant_isolation.py

RN-D01: los datos de un tenant nunca son accesibles desde otro.

Sin endpoint HTTP todavia (el panel in-app llega con el issue #31), el
aislamiento se verifica al nivel que este issue efectivamente construye:
`emit()` + RLS de la tabla `notifications`, mismo patron de
`tests/integration/db/test_tenant_isolation_capa0.py` (aislamiento
directo via `set_tenant_context`, no via HTTP).
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_session_factory, set_tenant_context
from adminprop.shared.notifications.service import emit

pytestmark = pytest.mark.asyncio


async def _emit_and_commit(seed, *, organization_id, role_id) -> None:
    await seed.add_member(organization_id=organization_id, role_id=role_id)
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, organization_id)
        await emit(
            session,
            organization_id=organization_id,
            event_type="work_order_created",
            payload={"work_order_id": str(uuid.uuid4())},
        )


class TestNotificationsTenantIsolation:
    async def test_tenant_a_only_sees_its_own_notifications(self, seed):
        org_a = await seed.create_org_with_roles()
        org_b = await seed.create_org_with_roles()
        await _emit_and_commit(
            seed, organization_id=org_a["organization_id"], role_id=org_a["roles"]["maintenance"]
        )
        await _emit_and_commit(
            seed, organization_id=org_b["organization_id"], role_id=org_b["roles"]["maintenance"]
        )

        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
            await set_tenant_context(session, org_a["organization_id"])
            result = await session.execute(sa.text("SELECT organization_id FROM notifications"))
            seen = {row[0] for row in result}

        assert seen == {org_a["organization_id"]}

    async def test_no_tenant_context_sees_no_notifications(self, seed):
        org = await seed.create_org_with_roles()
        await _emit_and_commit(
            seed, organization_id=org["organization_id"], role_id=org["roles"]["maintenance"]
        )

        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
            result = await session.execute(sa.text("SELECT organization_id FROM notifications"))
            rows = list(result)

        assert rows == []
