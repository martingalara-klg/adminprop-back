"""Fixtures compartidas de tests/integration/notifications (issue #11,
extendido en el #31 con `client`/JWT para el panel HTTP).

Mismo patron de engine fresco por test y `seed` duplicado deliberadamente
que `tests/integration/administracion/conftest.py` y
`tests/integration/shared/conftest.py` (evita "Future attached to a
different loop" entre tests async de pytest-asyncio; el `Seeder` no se
comparte via import cruzado entre paquetes de test, mismo criterio ya
documentado en ese conftest). `client`/`rsa_keypair`/`_auth_headers`
replican exactamente `tests/integration/maintenance/conftest.py` (mismo
criterio de duplicacion deliberada entre paquetes de test).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator

import pytest
import sqlalchemy as sa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient

from adminprop.config import get_settings
from adminprop.db.session import get_engine, get_session_factory
from adminprop.main import create_app
from adminprop.shared.auth import jwt as jwt_module
from adminprop.shared.auth.jwt import create_access_token
from adminprop.shared.cache.redis import get_redis_client


@pytest.fixture(autouse=True)
async def _fresh_engine_per_test() -> AsyncGenerator[None]:
    get_session_factory.cache_clear()
    get_engine.cache_clear()
    get_redis_client.cache_clear()
    yield
    engine = get_engine()
    await engine.dispose()
    get_session_factory.cache_clear()
    get_engine.cache_clear()
    redis = get_redis_client()
    await redis.flushdb()
    await redis.aclose()
    get_redis_client.cache_clear()


@pytest.fixture()
def rsa_keypair(tmp_path, monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    private_path = tmp_path / "private.pem"
    public_path = tmp_path / "public.pem"
    private_path.write_bytes(private_pem)
    public_path.write_bytes(public_pem)

    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", str(private_path))
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", str(public_path))
    get_settings.cache_clear()
    jwt_module.clear_key_cache()
    yield
    get_settings.cache_clear()
    jwt_module.clear_key_cache()


@pytest.fixture()
async def client(rsa_keypair) -> AsyncGenerator[AsyncClient]:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="https://testserver") as async_client:
        yield async_client


def _auth_headers(
    *, user_id: uuid.UUID, organization_id: uuid.UUID, role_name: str, permissions: list[str]
) -> dict[str, str]:
    token = create_access_token(
        user_id=user_id,
        organization_id=organization_id,
        role=role_name,
        permissions=permissions,
        is_super_admin=False,
        jti=str(uuid.uuid4()),
    )
    return {"Authorization": f"Bearer {token}"}


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
                await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
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
                await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
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
            role_name: str | None = None,
            permissions: list[str] | None = None,
        ) -> dict:
            """`role_name`/`permissions` son opcionales -- solo los tests
            HTTP del panel (issue #31) los necesitan para armar el JWT;
            los tests de enrutamiento (`emit()` directo, issue #11) siguen
            usando la firma original sin headers."""
            user_id = uuid.uuid4()
            email = email or _unique_email()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
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
            member = {"id": user_id, "email": email, "organization_id": organization_id}
            if role_name is not None:
                member["headers"] = _auth_headers(
                    user_id=user_id,
                    organization_id=organization_id,
                    role_name=role_name,
                    permissions=permissions or [],
                )
            return member

        async def insert_notification_row(
            self,
            *,
            organization_id: uuid.UUID,
            user_id: uuid.UUID,
            event_type: str = "work_order_created",
            read: bool = False,
        ) -> uuid.UUID:
            """Siembra una fila de `notifications` directamente (sin pasar
            por `emit()`) -- suficiente para los tests del panel (issue
            #31) que solo necesitan leer/marcar filas ya existentes.
            `read=True` la siembra ya leida (`read_at = now()` evaluado en
            SQL -- un literal Python "now()" se bindea como texto, no como
            funcion, y asyncpg lo rechaza contra una columna TIMESTAMPTZ)."""
            notification_id = uuid.uuid4()
            read_at_sql = "now()" if read else "NULL"
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
                await session.execute(
                    sa.text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                    {"tid": str(organization_id)},
                )
                await session.execute(
                    sa.text(
                        "INSERT INTO notifications "
                        "(id, organization_id, user_id, event_type, payload, read_at) "
                        f"VALUES (:id, :org_id, :user_id, :event_type, '{{}}'::jsonb, {read_at_sql})"
                    ),
                    {
                        "id": str(notification_id),
                        "org_id": str(organization_id),
                        "user_id": str(user_id),
                        "event_type": event_type,
                    },
                )
            return notification_id

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
            await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
            result = await session.execute(
                sa.text(
                    "SELECT id, user_id, event_type, payload, email_sent_at FROM notifications "
                    "WHERE organization_id = :organization_id ORDER BY created_at"
                ),
                {"organization_id": str(organization_id)},
            )
            return [dict(row._mapping) for row in result]

    return _fetch
