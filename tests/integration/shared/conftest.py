"""Fixtures compartidas de tests/integration/shared (issue #10).

Mismo patron de engine/session-factory fresco por test que el resto de
`tests/integration/*` (evita "Future attached to a different loop" entre
tests async de pytest-asyncio). El seed minimo se duplica deliberadamente
(mismo criterio documentado en `tests/integration/administracion/conftest.py`)
en vez de importar cruzado entre paquetes de test.
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


@pytest.fixture()
def seed(rsa_keypair):
    """Seed minimo: organizacion + usuario + rol + membresia + JWT."""

    class Seeder:
        async def create_org_with_member(
            self, *, permissions: list[str], role_name: str = "owner"
        ) -> dict:
            organization_id = uuid.uuid4()
            user_id = uuid.uuid4()
            role_id = uuid.uuid4()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
                await session.execute(
                    sa.text(
                        "INSERT INTO organizations (id, slug, name) VALUES (:id, :slug, :name)"
                    ),
                    {
                        "id": str(organization_id),
                        "slug": f"org-{organization_id.hex[:8]}",
                        "name": "Org de Test",
                    },
                )
                await session.execute(
                    sa.text(
                        "INSERT INTO users (id, email, password_hash, full_name, is_super_admin) "
                        "VALUES (:id, :email, :password_hash, :full_name, FALSE)"
                    ),
                    {
                        "id": str(user_id),
                        "email": f"user-{user_id.hex[:12]}@example.com",
                        "password_hash": "not-used-in-tests",
                        "full_name": "Test User",
                    },
                )
                await session.execute(
                    sa.text(
                        "INSERT INTO roles (id, organization_id, name, permissions) "
                        "VALUES (:id, :org_id, :name, :permissions)"
                    ).bindparams(sa.bindparam("permissions", type_=sa.JSON)),
                    {
                        "id": str(role_id),
                        "org_id": str(organization_id),
                        "name": role_name,
                        "permissions": json.dumps(permissions),
                    },
                )
                await session.execute(
                    sa.text(
                        "INSERT INTO organization_members "
                        "(organization_id, user_id, role_id, status) "
                        "VALUES (:org_id, :user_id, :role_id, 'active')"
                    ),
                    {
                        "org_id": str(organization_id),
                        "user_id": str(user_id),
                        "role_id": str(role_id),
                    },
                )

            token = create_access_token(
                user_id=user_id,
                organization_id=organization_id,
                role=role_name,
                permissions=permissions,
                is_super_admin=False,
                jti=str(uuid.uuid4()),
            )
            return {
                "organization_id": organization_id,
                "user_id": user_id,
                "headers": {"Authorization": f"Bearer {token}"},
            }

    return Seeder()


@pytest.fixture()
def audit_logs_reader():
    """Lee filas de `audit_logs` directamente (via `SET LOCAL ROLE
    adminprop_superadmin`, que tiene BYPASSRLS -- issue #42) para verificar
    que el INSERT ocurrio con los campos correctos."""

    async def _fetch(organization_id: uuid.UUID) -> list[dict]:
        session_factory = get_session_factory()
        async with session_factory() as session:
            await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
            result = await session.execute(
                sa.text(
                    "SELECT action, entity_type, entity_id, user_id, before_state, "
                    "after_state, request_id FROM audit_logs "
                    "WHERE organization_id = :organization_id ORDER BY created_at"
                ),
                {"organization_id": str(organization_id)},
            )
            return [dict(row._mapping) for row in result]

    return _fetch
