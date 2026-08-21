"""Fixtures compartidas de tests/integration/maintenance (issue #26).

Mismo patron de engine/session-factory fresco por test que
tests/integration/payments/conftest.py (evita "Future attached to a
different loop" entre tests async de pytest-asyncio). El `Seeder` se
duplica deliberadamente -- mismo criterio que el repo ya aplica entre
`auth`, `superadmin`, `administracion`, `people`, `properties`,
`contracts` y `payments`.
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
    # issue #26: aisla el storage de adjuntos por test (nunca el volumen
    # Docker real) -- mismo criterio que las claves JWT de arriba.
    monkeypatch.setenv("ATTACHMENTS_DIR", str(tmp_path / "attachments"))
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
            permissions = permissions if permissions is not None else ["work-order:read"]
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

        async def create_organization_with_system_roles(
            self, *, status: str = "active", name: str | None = None
        ) -> dict:
            """Siembra una organizacion `active` con sus 3 roles de sistema
            reales (`ROLE_DEFINITIONS`) -- `maintenance` solo tiene
            `work-order:read`/`quote`/`close` + `attachment:manage`
            (RN-A01)."""
            org_id = await self.create_organization(status=status, name=name)
            settings = dict(DEFAULT_ORGANIZATION_SETTINGS)
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "UPDATE organizations SET settings = :settings WHERE id = :id"
                    ).bindparams(sa.bindparam("settings", type_=sa.JSON)),
                    {"id": str(org_id), "settings": json.dumps(settings)},
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

        # ─── helpers propios de mantenimiento (issue #26) ──────────────────

        async def create_landlord_row(
            self, *, organization_id: uuid.UUID, name: str = "Propietario de prueba"
        ) -> uuid.UUID:
            landlord_id = uuid.uuid4()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO landlords (id, organization_id, name, commission_pct) "
                        "VALUES (:id, :org_id, :name, '10.00')"
                    ),
                    {"id": str(landlord_id), "org_id": str(organization_id), "name": name},
                )
            return landlord_id

        async def create_property_row(
            self,
            *,
            organization_id: uuid.UUID,
            landlord_id: uuid.UUID,
            address: str = "Av. Test 123",
            status: str = "available",
        ) -> uuid.UUID:
            property_id = uuid.uuid4()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO properties "
                        "(id, organization_id, landlord_id, address, property_type, status) "
                        "VALUES (:id, :org_id, :landlord_id, :address, 'departamento', :status)"
                    ),
                    {
                        "id": str(property_id),
                        "org_id": str(organization_id),
                        "landlord_id": str(landlord_id),
                        "address": address,
                        "status": status,
                    },
                )
            return property_id

        async def create_property(self, *, organization_id: uuid.UUID) -> uuid.UUID:
            """Atajo: propietario + propiedad de un solo golpe (los tests de
            mantenimiento no necesitan variar el propietario)."""
            landlord_id = await self.create_landlord_row(organization_id=organization_id)
            return await self.create_property_row(
                organization_id=organization_id, landlord_id=landlord_id
            )

        async def create_work_order_row(
            self,
            *,
            organization_id: uuid.UUID,
            property_id: uuid.UUID,
            created_by: uuid.UUID,
            title: str = "Arreglar caneria",
            payer: str = "agency",
            status: str = "open",
            settled_in_settlement_id: str | None = None,
        ) -> uuid.UUID:
            work_order_id = uuid.uuid4()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO work_orders "
                        "(id, organization_id, property_id, title, payer, status, "
                        "settled_in_settlement_id, created_by) "
                        "VALUES (:id, :org_id, :property_id, :title, :payer, :status, "
                        ":settled_in_settlement_id, :created_by)"
                    ),
                    {
                        "id": str(work_order_id),
                        "org_id": str(organization_id),
                        "property_id": str(property_id),
                        "title": title,
                        "payer": payer,
                        "status": status,
                        "settled_in_settlement_id": settled_in_settlement_id,
                        "created_by": str(created_by),
                    },
                )
            return work_order_id

        async def create_settlement_row(
            self, *, organization_id: uuid.UUID, generated_by: uuid.UUID
        ) -> uuid.UUID:
            """Issue #29: fila minima de `settlements` -- solo para poder
            referenciar `work_orders.settled_in_settlement_id` (FK real)
            en los tests de `is_work_order_settled` (RN-L04). No pasa por
            el flujo de calculo real (fuera de alcance de este modulo)."""
            landlord_id = uuid.uuid4()
            settlement_id = uuid.uuid4()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO landlords (id, organization_id, name, commission_pct) "
                        "VALUES (:id, :org_id, 'Propietario de prueba', 10.00)"
                    ),
                    {"id": str(landlord_id), "org_id": str(organization_id)},
                )
                await session.execute(
                    sa.text(
                        "INSERT INTO settlements "
                        "(id, organization_id, landlord_id, period, commission_pct_used, generated_by) "
                        "VALUES (:id, :org_id, :landlord_id, '2026-06-01', 10.00, :generated_by)"
                    ),
                    {
                        "id": str(settlement_id),
                        "org_id": str(organization_id),
                        "landlord_id": str(landlord_id),
                        "generated_by": str(generated_by),
                    },
                )
            return settlement_id

        async def create_quote_row(
            self,
            *,
            organization_id: uuid.UUID,
            work_order_id: uuid.UUID,
            submitted_by: uuid.UUID,
            amount: str = "1000.00",
            status: str = "submitted",
        ) -> uuid.UUID:
            quote_id = uuid.uuid4()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO work_order_quotes "
                        "(id, organization_id, work_order_id, amount, status, submitted_by) "
                        "VALUES (:id, :org_id, :work_order_id, :amount, :status, :submitted_by)"
                    ),
                    {
                        "id": str(quote_id),
                        "org_id": str(organization_id),
                        "work_order_id": str(work_order_id),
                        "amount": amount,
                        "status": status,
                        "submitted_by": str(submitted_by),
                    },
                )
            return quote_id

        async def set_approved_quote(
            self, *, work_order_id: uuid.UUID, quote_id: uuid.UUID
        ) -> None:
            """Wiring manual de `work_orders.approved_quote_id` -- los
            tests que siembran una cotizacion `status='approved'`
            directamente en DB (sin pasar por
            `WorkOrderQuoteService.approve`) necesitan setear esta FK a
            mano, igual que en el flujo real."""
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text("UPDATE work_orders SET approved_quote_id = :quote_id WHERE id = :id"),
                    {"quote_id": str(quote_id), "id": str(work_order_id)},
                )

        async def get_work_order(self, work_order_id: uuid.UUID) -> dict:
            session_factory = get_session_factory()
            async with session_factory() as session:
                result = await session.execute(
                    sa.text(
                        "SELECT status, final_cost, approved_quote_id FROM work_orders "
                        "WHERE id = :id"
                    ),
                    {"id": str(work_order_id)},
                )
                row = result.mappings().one()
                return dict(row)

        async def get_quote(self, quote_id: uuid.UUID) -> dict:
            session_factory = get_session_factory()
            async with session_factory() as session:
                result = await session.execute(
                    sa.text("SELECT status FROM work_order_quotes WHERE id = :id"),
                    {"id": str(quote_id)},
                )
                row = result.mappings().one()
                return dict(row)

        async def notification_rows(
            self, organization_id: uuid.UUID, event_type: str
        ) -> list[dict]:
            session_factory = get_session_factory()
            async with session_factory() as session:
                result = await session.execute(
                    sa.text(
                        "SELECT user_id, payload FROM notifications "
                        "WHERE organization_id = :org_id AND event_type = :event_type "
                        "ORDER BY created_at"
                    ),
                    {"org_id": str(organization_id), "event_type": event_type},
                )
                return [dict(row._mapping) for row in result]

        async def audit_rows(self, organization_id: uuid.UUID, action: str) -> list[dict]:
            session_factory = get_session_factory()
            async with session_factory() as session:
                result = await session.execute(
                    sa.text(
                        "SELECT entity_id, user_id, before_state, after_state FROM audit_logs "
                        "WHERE organization_id = :org_id AND action = :action ORDER BY created_at"
                    ),
                    {"org_id": str(organization_id), "action": action},
                )
                return [dict(row._mapping) for row in result]

    return Seeder()


@pytest.fixture()
def auth_headers():
    return _auth_headers


# Un jpg minimo (1x1) valido para content_type "image/jpeg" en los tests
# de attachments -- no necesita ser un jpg decodificable de verdad, el
# service solo valida `content_type` (no inspecciona bytes de imagen).
TINY_JPEG_BYTES = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300030202020202"
    "03020202030303030406040404040408060605060909080a0a090809090a0c"
    "0f0c0a0b0e0b09090d110d0e0f101011100a0c12131210130f101010ffc9000b"
    "080001000101011100ffcc0006001005050500ffda0008010100003f00d2cf"
    "20ffd9"
)
