"""Fixtures compartidas de tests/integration/administracion (issue #9).

Mismo patron de engine/session-factory fresco por test que
tests/integration/auth/conftest.py y tests/integration/superadmin/conftest.py
(evita "Future attached to a different loop" entre tests async de
pytest-asyncio). El `Seeder` se duplica deliberadamente (mismo criterio que
el repo ya aplica entre `auth` y `superadmin`) en vez de compartirse via
import cruzado entre paquetes de test.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient

from adminprop.config import get_settings
from adminprop.db.session import get_engine, get_session_factory
from adminprop.main import create_app
from adminprop.modules.superadmin.provisioning import (
    DEFAULT_ORGANIZATION_SETTINGS,
    ROLE_DEFINITIONS,
)
from adminprop.shared.auth import jwt as jwt_module
from adminprop.shared.auth.jwt import create_access_token
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


@pytest.fixture()
def sent_emails(monkeypatch) -> list[dict]:
    """RF-01: intercepta el encolado de la invitacion -- nunca Resend real
    en tests (docs/skills/testing.md).

    Parchea `notification_worker.send_transactional_email` (la fuente),
    no `administracion.service.send_transactional_email` -- issue #89
    convirtio ese ultimo en un import diferido DENTRO de
    `_send_invitation_email` para romper el ciclo de import de Celery, asi
    que ya no existe como atributo del modulo `administracion.service` al
    momento en que corre este fixture."""
    from adminprop.workers import notification_worker

    calls: list[dict] = []

    def _fake_delay(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(notification_worker.send_transactional_email, "delay", _fake_delay)
    return calls


def _unique_email() -> str:
    return f"user-{uuid.uuid4().hex[:12]}@example.com"


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


@pytest.fixture()
def seed(rsa_keypair):
    class Seeder:
        # issue #42: adminprop_app ya no bypassea RLS -- el seed/lectura de
        # datos cross-tenant necesita SET LOCAL ROLE adminprop_superadmin
        # explicito.
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
                await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
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
                await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
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
                await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
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

        async def create_invitation(
            self,
            *,
            organization_id: uuid.UUID,
            role_id: uuid.UUID,
            email: str | None = None,
            status: str = "pending",
            expires_in_hours: float = 72,
        ) -> str:
            email = email or _unique_email()
            raw_token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
            expires_at = datetime.now(UTC) + timedelta(hours=expires_in_hours)
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
                await session.execute(
                    sa.text(
                        "INSERT INTO organization_invitations "
                        "(organization_id, email, role_id, token, status, expires_at) "
                        "VALUES (:org_id, :email, :role_id, :token, :status, :expires_at)"
                    ),
                    {
                        "org_id": str(organization_id),
                        "email": email,
                        "role_id": str(role_id),
                        "token": token_hash,
                        "status": status,
                        "expires_at": expires_at,
                    },
                )
            return raw_token

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

        # ─── helpers propios de administracion (issue #9) ────────────────

        async def create_organization_with_system_roles(
            self, *, status: str = "active", name: str | None = None
        ) -> dict:
            """Siembra una organizacion `active` con sus 3 roles de sistema
            reales (`ROLE_DEFINITIONS` de `modules/superadmin/provisioning.py`)
            -- mismo catalogo de permisos que la organizacion real. Tambien
            siembra `settings` con `DEFAULT_ORGANIZATION_SETTINGS` (mismo
            default que `SuperAdminRepository.create_organization_with_roles`
            aplica en produccion -- `create_organization` de este Seeder no
            lo hace, quedaria `{}` sin este paso)."""
            org_id = await self.create_organization(status=status, name=name)
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
                await session.execute(
                    sa.text(
                        "UPDATE organizations SET settings = :settings WHERE id = :id"
                    ).bindparams(sa.bindparam("settings", type_=sa.JSON)),
                    {"id": str(org_id), "settings": json.dumps(DEFAULT_ORGANIZATION_SETTINGS)},
                )
            role_ids: dict[str, uuid.UUID] = {}
            for role_name, permissions in ROLE_DEFINITIONS:
                role_ids[role_name] = await self.create_role(
                    org_id, name=role_name, permissions=list(permissions)
                )
            return {"organization_id": org_id, "roles": role_ids}

        async def add_member(
            self,
            *,
            organization_id: uuid.UUID,
            role_id: uuid.UUID,
            role_name: str,
            status: str = "active",
            password: str = "Password1234",
            email: str | None = None,
        ) -> dict:
            """Crea un user + membresia en `organization_id` con el
            `role_id` dado, y devuelve tambien el JWT listo para usar."""
            user = await self.create_user(password=password, email=email)
            await self.create_membership(
                user_id=user["id"],
                organization_id=organization_id,
                role_id=role_id,
                status=status,
            )
            permissions = next((list(p) for name, p in ROLE_DEFINITIONS if name == role_name), [])
            headers = _auth_headers(
                user_id=user["id"],
                organization_id=organization_id,
                role_name=role_name,
                permissions=permissions,
            )
            return {
                **user,
                "organization_id": organization_id,
                "role_name": role_name,
                "headers": headers,
            }

    return Seeder()


@pytest.fixture()
def auth_headers():
    """Factory de headers JWT arbitrarios -- usado por tests que necesitan
    un rol/permissions especificos sin pasar por `seed.add_member` (ej:
    JWT de una organizacion que no existe en DB, para tests de
    aislamiento cross-tenant)."""
    return _auth_headers
