"""Fixtures compartidas de tests/integration/auth (issue #6).

Mismo patron de engine/session-factory fresco por test que
tests/integration/db/conftest.py (evita "Future attached to a different
loop" entre tests async de pytest-asyncio). Se agrega el equivalente para
el cliente Redis (usado por lockout/refresh_store/rate_limit) y un par de
claves RS256 efimero generado por test (nunca se commitea).
"""

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
from adminprop.shared.auth.passwords import hash_password
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


def _unique_email() -> str:
    return f"user-{uuid.uuid4().hex[:12]}@example.com"


@pytest.fixture()
def seed():
    class Seeder:
        def __init__(self) -> None:
            self.created_org_ids: list[uuid.UUID] = []
            self.created_user_ids: list[uuid.UUID] = []

        async def create_user(
            self,
            *,
            password: str = "Password1234",
            is_super_admin: bool = False,
            email: str | None = None,
        ) -> dict:
            user_id = uuid.uuid4()
            email = email or _unique_email()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO users (id, email, password_hash, full_name, is_super_admin) "
                        "VALUES (:id, :email, :password_hash, :full_name, :is_super_admin)"
                    ),
                    {
                        "id": str(user_id),
                        "email": email,
                        "password_hash": hash_password(password),
                        "full_name": "Test User",
                        "is_super_admin": is_super_admin,
                    },
                )
            self.created_user_ids.append(user_id)
            return {"id": user_id, "email": email, "password": password}

        async def create_organization(
            self, *, status: str = "active", name: str | None = None
        ) -> uuid.UUID:
            org_id = uuid.uuid4()
            name = name or f"Org {org_id.hex[:8]}"
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO organizations (id, slug, name, status) "
                        "VALUES (:id, :slug, :name, :status)"
                    ),
                    {
                        "id": str(org_id),
                        "slug": f"org-{org_id.hex[:8]}",
                        "name": name,
                        "status": status,
                    },
                )
            self.created_org_ids.append(org_id)
            return org_id

        async def create_role(
            self,
            organization_id: uuid.UUID,
            *,
            name: str = "owner",
            permissions: list[str] | None = None,
        ) -> uuid.UUID:
            role_id = uuid.uuid4()
            permissions = permissions if permissions is not None else ["contract:manage"]
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

        async def create_membership(
            self,
            *,
            user_id: uuid.UUID,
            organization_id: uuid.UUID,
            role_id: uuid.UUID,
            status: str = "active",
        ) -> None:
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
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

        async def create_active_member_with_org(
            self,
            *,
            password: str = "Password1234",
            role_name: str = "owner",
            permissions: list[str] | None = None,
        ) -> dict:
            user = await self.create_user(password=password)
            org_id = await self.create_organization()
            role_id = await self.create_role(org_id, name=role_name, permissions=permissions)
            await self.create_membership(
                user_id=user["id"], organization_id=org_id, role_id=role_id
            )
            return {
                **user,
                "organization_id": org_id,
                "role_id": role_id,
                "role_name": role_name,
            }

    return Seeder()
