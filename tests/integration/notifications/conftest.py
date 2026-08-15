"""Fixtures compartidas de tests/integration/notifications (issue #11).

Mismo patron de engine fresco por test y `seed` duplicado deliberadamente
que `tests/integration/administracion/conftest.py` y
`tests/integration/shared/conftest.py` (evita "Future attached to a
different loop" entre tests async de pytest-asyncio; el `Seeder` no se
comparte via import cruzado entre paquetes de test, mismo criterio ya
documentado en ese conftest).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_engine, get_session_factory


@pytest.fixture(autouse=True)
async def _fresh_engine_per_test() -> AsyncGenerator[None]:
    get_session_factory.cache_clear()
    get_engine.cache_clear()
    yield
    engine = get_engine()
    await engine.dispose()
    get_session_factory.cache_clear()
    get_engine.cache_clear()


def _unique_email() -> str:
    return f"user-{uuid.uuid4().hex[:12]}@example.com"


@pytest.fixture()
def seed():
    class Seeder:
        async def create_organization(self, *, name: str | None = None) -> uuid.UUID:
            org_id = uuid.uuid4()
            name = name or f"Org {org_id.hex[:8]}"
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO organizations (id, slug, name, status) "
                        "VALUES (:id, :slug, :name, 'active')"
                    ),
                    {"id": str(org_id), "slug": f"org-{org_id.hex[:8]}", "name": name},
                )
            return org_id

        async def create_role(
            self, organization_id: uuid.UUID, *, name: str, permissions: list[str] | None = None
        ) -> uuid.UUID:
            role_id = uuid.uuid4()
            permissions = permissions if permissions is not None else []
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO roles (id, organization_id, name, permissions) "
                        "VALUES (:id, :org_id, :name, :permissions)"
                    ).bindparams(sa.bindparam("permissions", type_=sa.JSON)),
                    {
                        "id": str(role_id),
                        "org_id": str(organization_id),
                        "name": name,
                        "permissions": json.dumps(permissions),
                    },
                )
            return role_id

        async def add_member(
            self,
            *,
            organization_id: uuid.UUID,
            role_id: uuid.UUID,
            status: str = "active",
            email: str | None = None,
        ) -> dict:
            user_id = uuid.uuid4()
            email = email or _unique_email()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO users (id, email, password_hash, full_name, is_super_admin) "
                        "VALUES (:id, :email, 'not-used-in-tests', 'Test User', FALSE)"
                    ),
                    {"id": str(user_id), "email": email},
                )
                await session.execute(
                    sa.text(
                        "INSERT INTO organization_members "
                        "(organization_id, user_id, role_id, status) "
                        "VALUES (:org_id, :user_id, :role_id, :status)"
                    ),
                    {
                        "org_id": str(organization_id),
                        "user_id": str(user_id),
                        "role_id": str(role_id),
                        "status": status,
                    },
                )
            return {"id": user_id, "email": email}

        async def create_org_with_roles(self) -> dict:
            """Siembra una organizacion con los 3 roles de sistema
            (`owner`, `admin`, `maintenance`) -- suficiente para ejercer
            `EVENT_RECIPIENT_ROLES` sin necesitar el catalogo real de
            permisos de `modules/superadmin/provisioning.py` (los tests
            de este modulo no ejercen RBAC, solo enrutamiento por
            nombre de rol)."""
            org_id = await self.create_organization()
            roles = {
                role_name: await self.create_role(org_id, name=role_name)
                for role_name in ("owner", "admin", "maintenance")
            }
            return {"organization_id": org_id, "roles": roles}

    return Seeder()


@pytest.fixture()
def notifications_reader():
    """Lee filas de `notifications` directamente (rol de conexion del
    pool -- superuser en test, BYPASSRLS) para verificar el resultado de
    `emit()` sin pasar por un endpoint HTTP (el panel in-app llega con
    el issue #31)."""

    async def _fetch(organization_id: uuid.UUID) -> list[dict]:
        session_factory = get_session_factory()
        async with session_factory() as session:
            result = await session.execute(
                sa.text(
                    "SELECT id, user_id, event_type, payload, email_sent_at FROM notifications "
                    "WHERE organization_id = :organization_id ORDER BY created_at"
                ),
                {"organization_id": str(organization_id)},
            )
            return [dict(row._mapping) for row in result]

    return _fetch
