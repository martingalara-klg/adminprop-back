"""Fixtures compartidas de tests/integration/superadmin (issue #7).

Mismo patron de engine/session-factory fresco por test que
tests/integration/auth/conftest.py (evita "Future attached to a different
loop" entre tests async de pytest-asyncio).
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
from adminprop.modules.superadmin import service as superadmin_service
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
async def super_admin_headers(rsa_keypair) -> dict[str, str]:
    """JWT `is_super_admin=true`, sin `org` ni `role` (RN-01).

    Issue #10: `OrganizationService` audita sus operaciones (`org.created`,
    `invitation.sent`, `org.disabled`/`org.enabled`) con
    `user_id=actor_user_id`, y `audit_logs.user_id` tiene
    `REFERENCES users(id)` (spec_data_model.md §Capa 7) -- antes alcanzaba
    con el claim del JWT (`requires_super_admin` no consulta `users`),
    pero ahora se siembra una fila real para que el INSERT de auditoria
    no viole la FK.
    """
    user_id = uuid.uuid4()
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text(
                "INSERT INTO users (id, email, password_hash, full_name, is_super_admin) "
                "VALUES (:id, :email, :password_hash, :full_name, TRUE)"
            ),
            {
                "id": str(user_id),
                "email": f"superadmin-{user_id.hex[:12]}@example.com",
                "password_hash": "not-used-in-tests",
                "full_name": "Super Admin Test",
            },
        )
    token = create_access_token(
        user_id=user_id,
        organization_id=None,
        role=None,
        permissions=[],
        is_super_admin=True,
        jti=str(uuid.uuid4()),
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
async def owner_headers(rsa_keypair) -> dict[str, str]:
    """JWT de un usuario `owner` de una organizacion (no super admin) --
    CA-00-05: debe recibir 403 SUPERADMIN_REQUIRED en /superadmin/*.

    Issue #10: el 403 ahora audita `access.denied` en `audit_logs`
    (`organization_id`/`user_id` con FK a `organizations`/`users`) -- se
    siembran filas reales que coinciden con los claims del JWT.
    """
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text("INSERT INTO organizations (id, slug, name) VALUES (:id, :slug, :name)"),
            {
                "id": str(organization_id),
                "slug": f"org-{organization_id.hex[:8]}",
                "name": "Org de Test CA-00-05",
            },
        )
        await session.execute(
            sa.text(
                "INSERT INTO users (id, email, password_hash, full_name, is_super_admin) "
                "VALUES (:id, :email, :password_hash, :full_name, FALSE)"
            ),
            {
                "id": str(user_id),
                "email": f"owner-{user_id.hex[:12]}@example.com",
                "password_hash": "not-used-in-tests",
                "full_name": "Owner Test",
            },
        )
    token = create_access_token(
        user_id=user_id,
        organization_id=organization_id,
        role="owner",
        permissions=["contract:manage", "user:manage"],
        is_super_admin=False,
        jti=str(uuid.uuid4()),
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def sent_emails(monkeypatch) -> list[dict]:
    """RF-03: intercepta el encolado de la invitacion -- nunca Resend real
    en tests (docs/skills/testing.md)."""
    calls: list[dict] = []

    def _fake_delay(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(superadmin_service.send_transactional_email, "delay", _fake_delay)
    return calls


@pytest.fixture()
def db_roles():
    """Helper de test: lee las filas de `roles` de una organizacion
    directamente (verificacion de invariante de seed, CA-00-01)."""

    async def _fetch(organization_id: str) -> list[dict]:
        session_factory = get_session_factory()
        async with session_factory() as session:
            result = await session.execute(
                sa.text(
                    "SELECT name, permissions, is_system_role FROM roles "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            )
            rows = [dict(row._mapping) for row in result]
            # asyncpg via `text()` crudo no de-serializa jsonb -- SQLAlchemy
            # solo aplica ese codec cuando la columna se tipa explicitamente
            # (ver repository.py que si usa `sa.bindparam(..., type_=sa.JSON)`
            # al escribir). Al leer con SQL crudo, `permissions` llega como
            # el string JSON crudo.
            for row in rows:
                if isinstance(row["permissions"], str):
                    row["permissions"] = json.loads(row["permissions"])
            return rows

    return _fetch
